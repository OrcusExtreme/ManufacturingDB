import json

log_file = r"C:\Users\knuser\.gemini\antigravity-ide\brain\c0229610-c2ce-46a3-913a-56fad3994ec0\.system_generated\logs\transcript_full.jsonl"
with open(log_file, 'r', encoding='utf-8') as f:
    for line in f:
        data = json.loads(line)
        if data.get('type') == 'TOOL_RESPONSE' and 'xml_parser.py' in data.get('content', ''):
            if 'Total Bytes' in data.get('content'):
                content = data['content']
                print(f"Found in step {data.get('step_index')}")
                if "feature_mapping =" not in content and "overall_length" not in content:
                    print("This looks like the old version!")
                    with open(r"d:\Project_Lab\Lab Database\Project\old_xml_parser.py", "w", encoding='utf-8') as out:
                        out.write(content)
                    break
