import os, azure.ai.agentserver.core.server as srv_pkg, inspect

srv_dir = os.path.dirname(inspect.getfile(srv_pkg))
print("server dir:", srv_dir)
port_tokens = ["8080", "8088", "8087", "port=", "PORT", ":port"]
for root, dirs, files in os.walk(srv_dir):
    for fn in files:
        if not fn.endswith(".py"):
            continue
        path = os.path.join(root, fn)
        try:
            lines = open(path, errors="replace").readlines()
        except Exception:
            continue
        for i, line in enumerate(lines):
            low = line.lower()
            if any(t.lower() in low for t in port_tokens):
                rel = os.path.relpath(path, srv_dir)
                print(f"  {rel}:{i+1}: {line.rstrip()}")
