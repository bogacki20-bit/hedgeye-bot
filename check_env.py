import pathlib
env = pathlib.Path(__file__).parent / ".env"
print(f"\nReading: {env}  (exists: {env.exists()})\n")
if env.exists():
    for line in env.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, val = line.partition("=")
        masked = (val[:18] + "...") if len(val) > 18 else "(short/empty)"
        print(f"  {name.strip():<28} = {masked}")
print()