import json

log_file = r"C:\Users\knuser\.gemini\antigravity-ide\brain\c0229610-c2ce-46a3-913a-56fad3994ec0\.system_generated\logs\transcript_full.jsonl"
with open(log_file, 'r', encoding='utf-8') as f:
    for line in f:
        data = json.loads(line)
        if data.get('type') == 'TOOL_RESPONSE' and 'xml_parser.py' in data.get('content', ''):
            if 'Total Bytes' in data.get('content'):
                content = data['content']
                if "feature_mapping" not in content:
                    lines = content.split('\n')
                    raw_code = []
                    started = False
                    for l in lines:
                        if l.startswith("1: "):
                            started = True
                        if started:
                            import re
                            match = re.match(r'^\d+:\s?(.*)', l)
                            if match:
                                raw_code.append(match.group(1))
                    if raw_code:
                        print("Found old xml_parser!")
                        with open(r"d:\Project_Lab\Lab Database\Project\backend\parsers\xml_parser.py", "w", encoding='utf-8') as out:
                            out.write('\n'.join(raw_code))
                        break
