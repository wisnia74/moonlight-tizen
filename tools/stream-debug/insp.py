"""Evaluate JavaScript in Moonlight's page on the TV: insp.py 'expr'  (or the expression on stdin).
Needs debug mode with the inspector forwarded to 127.0.0.1:9333."""
import importlib.util, sys, json
spec = importlib.util.spec_from_file_location("gl", __import__("os").path.join(__import__("os").path.dirname(__import__("os").path.abspath(__file__)), "..", "gamepad-latency.py"))
gl = importlib.util.module_from_spec(spec); spec.loader.exec_module(gl)
src = sys.argv[1] if len(sys.argv) > 1 else sys.stdin.read()
try:
    r = gl.Inspector(9333).evaluate(src)
except OSError as err:
    raise SystemExit(f"inspector not reachable on 127.0.0.1:9333: {err}")
print(r if isinstance(r, str) else json.dumps(r))
