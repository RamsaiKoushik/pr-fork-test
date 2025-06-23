import os
import sys
import re

def get_pull_request_diff():
    """
    Fetches the diff for the current pull request.
    Assumes running within a GitHub Actions environment.
    """
    pr_number = os.environ.get('GITHUB_REF').split('/')[-2]
    repo_owner = os.environ.get('GITHUB_REPOSITORY').split('/')[0]
    repo_name = os.environ.get('GITHUB_REPOSITORY').split('/')[1]

    # Use curl to fetch the diff from the GitHub API
    # Requires GITHUB_TOKEN to avoid rate limits for larger diffs
    # The 'Accept' header is crucial for getting the diff format
    command = f"curl -s -H 'Authorization: token {os.environ.get('GITHUB_TOKEN')}' -H 'Accept: application/vnd.github.v3.diff' 'https://api.github.com/repos/{repo_owner}/{repo_name}/pulls/{pr_number}'"
    
    print(f"Executing: {command.split(' ')[0]} ... {command.split(' ')[-1]}") # Print command without token
    
    import subprocess
    process = subprocess.run(command, shell=True, capture_output=True, text=True)
    if process.returncode != 0:
        print(f"Error fetching diff: {process.stderr}", file=sys.stderr)
        sys.exit(1)
    return process.stdout

def validate_diff(diff_content):
    """
    Validates the diff content based on the specified Arista patterns.
    Returns True if valid, False otherwise.
    """
    lines = diff_content.splitlines()
    in_arista_block = False
    new_line_block = [] # To store consecutive added lines for block validation
    errors = []

    # Regex patterns
    ARISTA_BEGIN_COMMENT = re.compile(r'^\s*\/\/ Arista Begin\s*$')      # C-style comment
    ARISTA_END_COMMENT = re.compile(r'^\s*\/\/ Arista End\s*$')        # C-style comment
    ARISTA_CONFIDENTIAL = re.compile(r'Arista confidential\.')

    # Determine comment style based on file extension
    current_file_path = None
    file_comment_patterns = {
        '.py': ('#', '#', '#'), # (line_comment_start, block_comment_start, block_comment_end)
        '.js': ('//', '/*', '*/'),
        '.ts': ('//', '/*', '*/'),
        '.java': ('//', '/*', '*/'),
        '.c': ('//', '/*', '*/'),
        '.cpp': ('//', '/*', '*/'),
        '.h': ('//', '/*', '*/'),
        '.hpp': ('//', '/*', '*/'),
        '.cs': ('//', '/*', '*/'),
        '.sh': ('#', '#', '#'), # For shell scripts
        '.go': ('//', '/*', '*/'),
        '.rs': ('//', '/*', '*/'),
        '.html': (''),
        '.xml': (''),
        '.css': ('/*', '/*', '*/'),
        # Add more as needed. Default to C-style comments if unknown.
    }
    
    def get_comment_chars(file_path):
        _, ext = os.path.splitext(file_path)
        return file_comment_patterns.get(ext.lower(), ('//', '/*', '*/')) # Default to C-style

    line_comment_start, block_comment_start, block_comment_end = ('//', '/*', '*/') # Default

    for i, line in enumerate(lines):
        line_num = i + 1
        
        # New file header
        if line.startswith('+++ b/'):
            current_file_path = line[6:].strip()
            line_comment_start, block_comment_start, block_comment_end = get_comment_chars(current_file_path)
            ARISTA_BEGIN_COMMENT = re.compile(rf'^\s*{re.escape(line_comment_start)}\s*Arista Begin\s*$')
            ARISTA_END_COMMENT = re.compile(rf'^\s*{re.escape(line_comment_start)}\s*Arista End\s*$')
            continue

        # Only process added lines
        if not line.startswith('+') or line.startswith('+++'): # Exclude `+++ b/path/to/file` lines
            if new_line_block: # Process the accumulated block before moving on
                if in_arista_block:
                    errors.append(f"Line {line_num}: Expected 'Arista End' comment, but block ended without it.")
                    in_arista_block = False
                validate_new_block(new_line_block, line_comment_start, block_comment_start, block_comment_end, errors)
                new_line_block = []
            continue

        actual_line = line[1:].strip() # Remove the '+' prefix

        # Detect Arista Begin/End comments using determined style
        is_arista_begin = ARISTA_BEGIN_COMMENT.search(line)
        is_arista_end = ARISTA_END_COMMENT.search(line)

        if is_arista_begin:
            if in_arista_block:
                errors.append(f"Line {line_num}: Nested 'Arista Begin' comment found at '{line}'.")
            in_arista_block = True
            if new_line_block: # Process any accumulated block before this new begin
                validate_new_block(new_line_block, line_comment_start, block_comment_start, block_comment_end, errors)
                new_line_block = []
            continue
        elif is_arista_end:
            if not in_arista_block:
                errors.append(f"Line {line_num}: 'Arista End' comment found without a preceding 'Arista Begin' at '{line}'.")
            in_arista_block = False
            if new_line_block: # Process the block that just ended
                validate_new_block(new_line_block, line_comment_start, block_comment_start, block_comment_end, errors)
                new_line_block = []
            continue
        
        # Accumulate added lines for block validation outside specific Arista comments
        new_line_block.append((actual_line, line_num))
    
    # After loop, check if we're still in an Arista block or if there's a pending new_line_block
    if in_arista_block:
        errors.append("Reached end of diff but 'Arista Begin' block was not closed with 'Arista End'.")
    if new_line_block:
        validate_new_block(new_line_block, line_comment_start, block_comment_start, block_comment_end, errors)

    if errors:
        print("\n--- DIFF VALIDATION ERRORS ---", file=sys.stderr)
        for err in errors:
            print(err, file=sys.stderr)
        print("----------------------------", file=sys.stderr)
        return False
    else:
        print("\n--- DIFF VALIDATION PASSED ---")
        return True

def validate_new_block(block_lines, line_comment_start, block_comment_start, block_comment_end, errors):
    """
    Validates a block of consecutive new lines for Arista confidential comments.
    """
    if not block_lines:
        return

    first_line_content, first_line_num = block_lines[0]
    last_line_content, last_line_num = block_lines[-1]
    
    # Remove leading comment markers for content check
    clean_first_line = first_line_content.lstrip(line_comment_start).strip()
    
    # Check for "Arista confidential."
    if not re.search(r'Arista confidential\.', first_line_content):
        errors.append(f"Line {first_line_num}: First line of new code block must contain 'Arista confidential.' comment. Found: '{first_line_content}'")
    
    # Check for Arista Begin/End only if the block is not enclosed in them
    # This function is called for blocks *between* Arista Begin/End, or blocks that are not enclosed.
    # The primary loop handles strict enforcement of Arista Begin/End pairs.
    
    # If it's a single-line block, Arista confidential should be on that line
    if len(block_lines) == 1:
        # The check above for 'Arista confidential.' already covers this
        pass # No additional check needed specific to single line beyond the general confidential check
    else: # Multiple lines
        # Here we only need to ensure 'Arista confidential.' is on the first line.
        # The 'Arista Begin/End' for multi-line blocks are enforced by the main loop's `in_arista_block` state.
        pass


if __name__ == "__main__":
    if os.environ.get('GITHUB_EVENT_NAME') != 'pull_request':
        print("This script is intended to run only on pull_request events.", file=sys.stderr)
        sys.exit(0) # Exit gracefully if not a PR

    github_token = os.environ.get('GITHUB_TOKEN')
    if not github_token:
        print("Error: GITHUB_TOKEN environment variable is not set.", file=sys.stderr)
        sys.exit(1)

    diff_data = get_pull_request_diff()
    print("\n--- RAW DIFF CONTENT ---")
    print(diff_data)
    print("----------------------\n")

    if validate_diff(diff_data):
        print("Diff validation successful!")
        sys.exit(0)
    else:
        print("Diff validation failed!", file=sys.stderr)
        sys.exit(1)