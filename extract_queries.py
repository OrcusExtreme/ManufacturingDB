import re
def dump(fpath):
    print(f"\n--- {fpath} ---")
    text = open(fpath, encoding='utf-8').read()
    matches = re.finditer(r'([a-zA-Z0-9_]+)\s*=\s*(f?\"\"\"|\'\'\')(.*?)(?:\"\"\"|\'\'\')', text, re.DOTALL)
    for m in matches:
        var_name = m.group(1)
        query = m.group(3).strip()
        if 'SELECT ' in query.upper():
            print(f"[{var_name}]\n{query[:150]}...\n")

dump('frontend/user_dashboard.py')
dump('frontend/admin_dashboard.py')
