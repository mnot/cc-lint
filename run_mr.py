# Helper script to run MR job locally
import sys
import shlex


from cc_lint.mr import CCLintJob

if __name__ == '__main__':
    # Emulate command line execution provided by mrjob
    # This invokes the CCLintJob.run() method which parses sys.argv

    # Handle --limit argument wrapper
    import argparse
    import tempfile
    import atexit
    import os

    # 1. Get the full parser from CCLintJob to understand structure
    job_dummy = CCLintJob(args=[])
    parser = job_dummy.arg_parser
    parser.add_argument('--limit', type=int, help='Limit processing to N records')
    
    # 2. Parse full args to identify input files and limit
    # We use parse_known_args in case there are truly unknown args (though mrjob usually grabs everything)
    options, _ = parser.parse_known_args(sys.argv[1:])
    
    limit = options.limit
    input_files = options.args
    
    # 3. Create a clean argv without --limit
    # We use a Limit-only parser to Strip --limit safely
    limit_parser = argparse.ArgumentParser(add_help=False)
    limit_parser.add_argument('--limit', type=int)
    # We also add -l alias if supported, but let's stick to simple stripping or use unknown
    # parse_known_args returns (namespace, unknown_args_list)
    # unknown_args_list preserves order and structure of everything ELSE
    _, clean_argv = limit_parser.parse_known_args(sys.argv[1:])
    
    final_args = []
    
    if limit is not None and limit > 0:
        # 4. Filter input files out of clean_argv
        # We need to know which tokens are flags vs values
        
        skip_next = False
        inputs_to_remove = set(input_files)
        
        for i, token in enumerate(clean_argv):
            if skip_next:
                final_args.append(token)
                skip_next = False
                continue
                
            if token.startswith('-'):
                # It's a flag
                final_args.append(token)
                
                # Check if it takes an argument
                # We look up in the FULL parser
                action = parser._option_string_actions.get(token)
                if action and action.nargs != 0 and action.const is None and not isinstance(action, (argparse._StoreTrueAction, argparse._StoreFalseAction, argparse._StoreConstAction)):
                     # Heuristic: if nargs is None (default 1) or > 0, it takes args
                     # StoreTrue/False don't take args.
                     skip_next = True
            else:
                # Positional arg (or invalid flag value)
                if token in inputs_to_remove:
                    # It's an input file, skip it (we'lll replace)
                    pass
                else:
                    final_args.append(token)

        # 5. Process limit
        lines = []
        count = 0
        
        if not input_files:
            # Stdin case
             try:
                for line in sys.stdin:
                    lines.append(line)
                    count += 1
                    if count >= limit:
                        break
             except KeyboardInterrupt:
                pass
        else:
            for fpath in input_files:
                try:
                    with open(fpath, 'r') as f:
                        for line in f:
                            lines.append(line)
                            count += 1
                            if count >= limit:
                                break
                except Exception as e:
                    print(f"Error reading {fpath}: {e}")
                    sys.exit(1)
                if count >= limit:
                    break
        
        # Create temp file
        tf = tempfile.NamedTemporaryFile(mode='w', delete=False)
        tf.writelines(lines)
        tf.close()
        
        def cleanup():
            if os.path.exists(tf.name):
                os.remove(tf.name)
        atexit.register(cleanup)
        
        final_args.append(tf.name)
        
        # Execute job with correct method
        CCLintJob(args=final_args).execute()
        
    else:
        # No limit, just run with clean args (limit stripped)
        CCLintJob(args=clean_argv).execute()
