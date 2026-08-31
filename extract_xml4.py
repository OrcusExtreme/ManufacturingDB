import json

log_file = r"C:\Users\knuser\.gemini\antigravity-ide\brain\c0229610-c2ce-46a3-913a-56fad3994ec0\.system_generated\logs\transcript_full.jsonl"
with open(log_file, 'r', encoding='utf-8') as f:
    for line in f:
        data = json.loads(line)
        content = data.get('content', '')
        if isinstance(content, str) and 'def parse_xml(' in content and 'WorkModel' in content:
            print("Found potential original xml_parser in step", data.get('step_index'))
            with open(r"d:\Project_Lab\Lab Database\Project\found_xml_parser.py", "w", encoding='utf-8') as out:
                # Assuming the content might have line numbers like "1: "
                lines = content.split('\n')
                for l in lines:
                    import re
                    match = re.match(r'^\d+:\s?(.*)', l)
                    if match:
                        out.write(match.group(1) + '\n')
                    else:
                        out.write(l + '\n')
            break
