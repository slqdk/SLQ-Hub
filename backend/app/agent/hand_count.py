"""Finds the hands in a picture, on the CPU, and prints them as JSON. Run by media_check.count_hands as a process of its
own: a C++ library that dies takes this with it and not the Hub. In about 150 ms, with no model loaded on the card.

A language model knows how many hands a person has, and says so: chat 298's Gemma 4 counted "arms": 2, "hands": 2 for
a picture with three. A hand detector has no such idea (0.80.4). Run with a confidence of 0.3: it should rather find too
many than too few.

The whole picture alone misses hands (0.80.6): on chat 299's picture, arms crossed and a third hand on a thigh, it found
one of the three. Four overlapping parts found all three, and on chat 292's a shoulder, which is why a part's hand needs
a second part. MediaPipe's HandLandmarker on the 35 different pictures of Søren's that had reached here: the whole
picture gave none on 3, one on 9, two on 21 and three on 2; with the parts none on 2, one on 7, two on 23 and three on 3,
and those three are the three with a third hand."""
import json
import sys
import types

PARTS = ((0, 0, .65, .6), (.35, 0, 1, .6), (0, .4, .65, 1), (.35, .4, 1, 1))    # left, top, right, bottom: they overlap
SAME_HAND = 0.25                    # this much of the smaller box shared: one hand


def area(box: list) -> float:
    return (box[2] - box[0]) * (box[3] - box[1])


def shared(a: list, b: list) -> float:
    """How much of the smaller of two boxes (left, top, right, bottom) lies in the other."""
    wide, high = min(a[2], b[2]) - max(a[0], b[0]), min(a[3], b[3]) - max(a[1], b[1])
    smaller = min(area(a), area(b))
    return max(0, wide) * max(0, high) / smaller if smaller > 0 else 0.0


def in_picture(points: list[tuple], part: tuple) -> list[float]:
    """The box round a hand's points, from a part of the picture to the whole of it."""
    left, top, right, bottom = part
    xs = [left + max(0.0, min(1.0, x)) * (right - left) for x, _ in points]
    ys = [top + max(0.0, min(1.0, y)) * (bottom - top) for _, y in points]
    return [round(min(xs), 4), round(min(ys), 4), round(max(xs), 4), round(max(ys), 4)]


def merged(whole: list[dict], parts: list[list[dict]]) -> list[dict]:
    """The whole picture's hands, and those it missed: a part's hand that another part found too, where nothing has been
    found yet of either side. The two hands of a grip overlap, so they can only come from the whole picture."""
    hands = list(whole)
    seen = [(i, hand) for i, part in enumerate(parts) for hand in part]
    for i, hand in sorted(seen, key=lambda s: -area(s[1]["box"])):
        again = any(j != i and shared(hand["box"], other["box"]) >= SAME_HAND for j, other in seen)
        if again and not any(shared(hand["box"], h["box"]) >= SAME_HAND for h in hands):
            hands.append(hand)
    return hands


def find(path: str, model: str) -> list[dict]:
    # `import mediapipe` pulls in its drawing helpers, which want OpenCV and matplotlib (120 MB): not used here
    for name in ("cv2", "matplotlib", "matplotlib.pyplot"):
        sys.modules.setdefault(name, types.ModuleType(name))
    import mediapipe as mp
    import numpy as np
    from mediapipe.tasks.python import BaseOptions
    from mediapipe.tasks.python.vision import HandLandmarker, HandLandmarkerOptions, RunningMode
    from PIL import Image, ImageOps

    with Image.open(path) as img:
        pixels = np.array(ImageOps.exif_transpose(img).convert("RGB"))
    options = HandLandmarkerOptions(
        base_options=BaseOptions(model_asset_path=model, delegate=BaseOptions.Delegate.CPU),
        running_mode=RunningMode.IMAGE, num_hands=8, min_hand_detection_confidence=0.3,
        min_hand_presence_confidence=0.3, min_tracking_confidence=0.3)
    high, wide = pixels.shape[:2]

    def look(detector, part: tuple) -> list[dict]:
        left, top, right, bottom = part
        seen = np.ascontiguousarray(pixels[int(top * high):int(bottom * high), int(left * wide):int(right * wide)])
        found = detector.detect(mp.Image(image_format=mp.ImageFormat.SRGB, data=seen))
        return [{"box": in_picture([(p.x, p.y) for p in marks], part), "side": side[0].category_name[:1]}
                for marks, side in zip(found.hand_landmarks, found.handedness)]

    with HandLandmarker.create_from_options(options) as detector:
        return merged(look(detector, (0, 0, 1, 1)), [look(detector, part) for part in PARTS])


if __name__ == "__main__":
    print(json.dumps(find(sys.argv[1], sys.argv[2])))
