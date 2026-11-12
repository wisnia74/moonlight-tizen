"""an-refine.py FILE...: what the stream spent on getting the picture back after each keyframe, in saved m.py
measurements. One line per keyframe: the usual frame size before it, the keyframe, the sizes of the frames behind it,
and how many frames and KB it took until frame sizes were back at the size from before the keyframe.
A keyframe on a picture the encoder had refined for a while shows a long tail; one on a moving picture shows none."""
import json, statistics as st, sys

for path in sys.argv[1:]:
    d = json.load(open(path))["data"]; fr = d["frames"]; t0 = fr[0][3]
    keys = [k for k, x in enumerate(fr) if x[8] and 130 <= k < len(fr) - 130]
    print(f"{path}: {len(fr)} frames, {(fr[-1][3] - t0) / 1000:.0f} s, {len(keys)} keyframes")
    for k in keys:
        pre = st.median(x[2] for x in fr[k - 120:k]); post = [x[2] for x in fr[k + 1:k + 121]]
        fps = 120 / ((fr[k][3] - fr[k - 120][3]) / 1000)
        n = next((i for i in range(len(post) - 5) if st.median(post[i:i + 5]) <= 1.25 * pre), len(post))
        extra = sum(max(0, s - pre) for s in post[:n])
        print(f"  at {(fr[k][3] - t0) / 1000:5.0f} s  {fps:3.0f} fps  frame before {pre / 1000:5.1f} KB  keyframe {fr[k][2] / 1000:5.1f} KB ({fr[k][2] / pre:4.1f}x)  next 1/2/3/5/10/20: "
              + "/".join(f"{post[i - 1] / 1000:.0f}" for i in (1, 2, 3, 5, 10, 20)) + f" KB  back to normal after {n} frames, {extra / 1000:.0f} KB extra")
