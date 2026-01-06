#!/usr/bin/env python3
import sys
import json

def main():
    """
    Reads line-delimited JSON from stdin (mrjob output),
    collates it into a single JSON object with 'total_responses',
    'field_counts', and a 'notes' dictionary for everything else.
    """
    output = {
        "notes": {}
    }

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            # mrjob output is [key, value]
            key, value = json.loads(line)
            
            if key == "total_responses":
                output["total_responses"] = value
            elif key == "field_counts":
                output["field_counts"] = value
            elif key == "unprocessed_counts":
                output["unprocessed_counts"] = value
            else:
                # Everything else is considered a note/stat
                output["notes"][key] = value
                
        except json.JSONDecodeError:
            sys.stderr.write(f"Error decoding line: {line}\n")
        except ValueError:
            sys.stderr.write(f"Error parsing line (expected [key, value]): {line}\n")

    print(json.dumps(output, indent=2))

if __name__ == "__main__":
    main()
