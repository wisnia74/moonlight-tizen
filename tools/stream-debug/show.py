import json, sys
for l in open(sys.argv[1]):
    r = json.loads(l)
    print(f"{r['label']:20s} stalls {r['recvStalls']:4d} ({r['recvStallMs']:5d} ms, >=16: {r['recvStalls16']:3d})  late12 {r['late12']:3d} late25 {r['late25']:3d}  underflow {r['underflow']}  ahead p50 {r['aheadP50']} min {r['aheadMin']}  dip {r['dip']}  fps {r['sentFps']}/{r['inFps']}")
