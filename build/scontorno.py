import io
import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image
from rembg import new_session, remove

CANVAS_W, CANVAS_H = 1000, 1250
BANNER_CROP_RATIO = 0.85  # remove bottom blue name/agency banner before segmentation

# Target framing, expressed relative to the distance between the eyes (very
# stable across faces, unlike raw face-box height which varies a lot with
# glasses/beard/hairstyle). This is a floor: the canvas is always fully
# covered (cropping left/right/bottom as needed), so the actual scale can
# end up larger than this if the person's cutout is too short to reach the
# bottom of the canvas otherwise.
EYE_LINE_RATIO = 0.40          # vertical position of the eye-line on the canvas
INTEROCULAR_TARGET_PX = 175    # desired distance between the eyes, in canvas px

# fallback framing (used only if no face at all is detected)
FALLBACK_TOP_MARGIN_RATIO = 0.04

session = new_session("u2net_human_seg")
_haar_dir = cv2.data.haarcascades
_face_cascade = cv2.CascadeClassifier(_haar_dir + "haarcascade_frontalface_default.xml")
_eye_cascade = cv2.CascadeClassifier(_haar_dir + "haarcascade_eye_tree_eyeglasses.xml")
_eye_cascade_plain = cv2.CascadeClassifier(_haar_dir + "haarcascade_eye.xml")


def _detect_faces(gray: np.ndarray, max_people=2):
    """Return up to max_people non-overlapping face boxes, largest first."""
    faces = _face_cascade.detectMultiScale(gray, scaleFactor=1.03, minNeighbors=5, minSize=(80, 80))
    if len(faces) == 0:
        return []
    faces = sorted(faces, key=lambda f: f[2] * f[3], reverse=True)
    kept = [faces[0]]
    for f in faces[1:]:
        fx, fy, fw, fh = f
        overlaps = any(
            fx < kx + kw and fx + fw > kx and fy < ky + kh and fy + fh > ky
            for kx, ky, kw, kh in kept
        )
        if not overlaps:
            kept.append(f)
        if len(kept) >= max_people:
            break
    return kept


def _detect_eyes(gray: np.ndarray, face_box):
    fx, fy, fw, fh = face_box
    pad = int(fh * 0.15)
    y0, y1 = max(0, fy - pad), min(gray.shape[0], fy + fh + pad)
    x0, x1 = max(0, fx - pad), min(gray.shape[1], fx + fw + pad)
    roi = gray[y0:y1, x0:x1]

    for cascade in (_eye_cascade, _eye_cascade_plain):
        eyes = cascade.detectMultiScale(roi, scaleFactor=1.05, minNeighbors=8, minSize=(20, 20))
        eyes = [e for e in eyes if e[1] + e[3] / 2 < roi.shape[0] * 0.7]
        if len(eyes) >= 2:
            eyes = sorted(eyes, key=lambda e: e[2] * e[3], reverse=True)[:2]
            eyes = sorted(eyes, key=lambda e: e[0])
            (ex1, ey1, ew1, eh1), (ex2, ey2, ew2, eh2) = eyes
            cx1, cy1 = x0 + ex1 + ew1 / 2, y0 + ey1 + eh1 / 2
            cx2, cy2 = x0 + ex2 + ew2 / 2, y0 + ey2 + eh2 / 2
            return (cx1, cy1), (cx2, cy2)
    return None


def _face_eye_geometry(gray: np.ndarray, face_box):
    """Return (eye_mid_x, eye_line_y, interocular_px) for one face box."""
    fx, fy, fw, fh = face_box
    eyes = _detect_eyes(gray, face_box)
    if eyes:
        (x1, y1), (x2, y2) = eyes
        interocular = abs(x2 - x1)
        if 0.25 * fw <= interocular <= 0.9 * fw:
            return (x1 + x2) / 2, (y1 + y2) / 2, interocular
    # fallback: typical frontal-face anthropometry
    return fx + fw / 2, fy + 0.40 * fh, 0.45 * fw


def _scene_geometry(rgb_image: Image.Image):
    """Return (eye_mid_x, eye_line_y, interocular_px, n_people) for the whole photo, or None."""
    gray = np.array(rgb_image.convert("L"))
    faces = _detect_faces(gray)
    if not faces:
        return None
    people = [_face_eye_geometry(gray, f) for f in faces]
    eye_mid_x = sum(p[0] for p in people) / len(people)
    eye_line_y = sum(p[1] for p in people) / len(people)
    interocular = sum(p[2] for p in people) / len(people)
    return eye_mid_x, eye_line_y, interocular, len(people)


def process(src_path: Path, dst_path: Path) -> bool:
    """Process one photo. Returns True if a face was detected and used to align it."""
    src_img = Image.open(src_path).convert("RGB")
    w, h = src_img.size
    src_img = src_img.crop((0, 0, w, int(h * BANNER_CROP_RATIO)))

    geometry = _scene_geometry(src_img)

    buf = io.BytesIO()
    src_img.save(buf, format="PNG")
    out_bytes = remove(buf.getvalue(), session=session)
    img = Image.open(io.BytesIO(out_bytes)).convert("RGBA")

    alpha = img.split()[-1]
    bbox = alpha.getbbox()
    if bbox is None:
        img.save(dst_path)
        return False
    cropped = img.crop(bbox)
    cw, ch = cropped.size

    canvas = Image.new("RGBA", (CANVAS_W, CANVAS_H), (0, 0, 0, 0))

    if geometry:
        eye_mid_x, eye_line_y, interocular, n_people = geometry
        eye_mid_x -= bbox[0]
        eye_line_y -= bbox[1]

        eye_scale = INTEROCULAR_TARGET_PX / interocular
        eps = 1e-6

        horizontal_fit_scale = CANVAS_W / cw
        vertical_fit_scale = CANVAS_H / ch

        if n_people >= 2:
            # Group photo: never crop sideways (nobody should fall out of
            # frame). Zoom in as much as the eye target asks for, capped at
            # "everyone still fits horizontally".
            scale = min(horizontal_fit_scale, max(eye_scale, vertical_fit_scale))
        else:
            # Single portrait: fill the canvas edge to edge (never smaller
            # than a full-width or full-height fit), without ever forcing
            # the scale beyond that just to keep the eyes dead-center - that
            # blows up the zoom whenever the person isn't perfectly centered
            # in their own cutout.
            scale = max(eye_scale, horizontal_fit_scale, vertical_fit_scale)

        new_w, new_h = max(1, round(cw * scale)), max(1, round(ch * scale))
        resized = cropped.resize((new_w, new_h), Image.LANCZOS)

        # Aim for the eye target on each axis, but only where that axis
        # actually covers the canvas: clamp within the valid (no-gap) range
        # there, or simply center the axis that falls short (group photos
        # capped by horizontal_fit_scale can end up shorter than the canvas
        # on one axis - centering that leftover margin looks intentional,
        # instead of a raw eye-driven offset dragging it to one side).
        if new_w >= CANVAS_W:
            paste_x = round(CANVAS_W / 2 - eye_mid_x * scale)
            paste_x = max(min(paste_x, 0), CANVAS_W - new_w)
        else:
            paste_x = (CANVAS_W - new_w) // 2
        if new_h >= CANVAS_H:
            paste_y = round(CANVAS_H * EYE_LINE_RATIO - eye_line_y * scale)
            paste_y = max(min(paste_y, 0), CANVAS_H - new_h)
        else:
            paste_y = (CANVAS_H - new_h) // 2
        canvas.paste(resized, (paste_x, paste_y), resized)
    else:
        # no face at all: cover the canvas using the cutout's own bounding box
        scale = max(CANVAS_W / cw, CANVAS_H / ch)
        new_w, new_h = max(1, round(cw * scale)), max(1, round(ch * scale))
        resized = cropped.resize((new_w, new_h), Image.LANCZOS)
        x = (CANVAS_W - new_w) // 2
        y = round(CANVAS_H * FALLBACK_TOP_MARGIN_RATIO)
        canvas.paste(resized, (x, y), resized)

    canvas.save(dst_path)
    return geometry is not None


if __name__ == "__main__":
    src_dir = Path(sys.argv[1])
    dst_dir = Path(sys.argv[2])
    dst_dir.mkdir(parents=True, exist_ok=True)
    names = sys.argv[3:]
    files = [src_dir / n for n in names] if names else sorted(src_dir.glob("*"))
    senza_volto = []
    for f in files:
        if f.suffix.lower() not in (".jpg", ".jpeg", ".png", ".webp"):
            continue
        out = dst_dir / (f.stem + ".png")
        trovato = process(f, out)
        print(("[volto OK] " if trovato else "[FALLBACK] ") + f.name + " -> " + out.name)
        if not trovato:
            senza_volto.append(f.name)
    if senza_volto:
        print("\nFoto senza volto rilevato (framing di fallback, da controllare):")
        for n in senza_volto:
            print(" -", n)
