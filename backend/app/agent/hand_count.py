"""Finds the hands in a picture, on the CPU, and prints them as JSON. Run by media_check.count_hands as a process of its
own: a C++ library that dies takes this with it and not the Hub. In about 50 ms, with no model loaded on the card.

A language model knows how many hands a person has, and says so: chat 298's Gemma 4 counted "arms": 2, "hands": 2 for
a picture with three. A hand detector has no such idea (0.80.4). MediaPipe's HandLandmarker, on the 26 pictures of
Søren's that had reached here: one with no hand found, six with one, seventeen with two and two with three - the two
that have a third hand. Run with a confidence of 0.3: it should rather find too many than too few."""
import json
import sys
import types


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
    with HandLandmarker.create_from_options(options) as detector:
        found = detector.detect(mp.Image(image_format=mp.ImageFormat.SRGB, data=pixels))
    hands = []
    for marks, side in zip(found.hand_landmarks, found.handedness):
        xs, ys = [max(0.0, min(1.0, p.x)) for p in marks], [max(0.0, min(1.0, p.y)) for p in marks]
        hands.append({"box": [round(min(xs), 4), round(min(ys), 4), round(max(xs), 4), round(max(ys), 4)],
                      "side": side[0].category_name[:1]})
    return hands


if __name__ == "__main__":
    print(json.dumps(find(sys.argv[1], sys.argv[2])))
