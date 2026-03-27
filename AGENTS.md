@knowledge-base/agent_cli_file/catalogue.md
@.ai/rules/spec-review.md

## Output Path Rule

When a prompt explicitly provides absolute output paths (e.g., `E:\docker_data\...\output.md`
or `{OUTPUT_DIR}` style variables resolved to absolute paths):

1. ALWAYS write output files to those exact absolute paths.
2. NEVER write to any path under `projects/`, `openspec/`, or any project subdirectory.
3. NEVER use relative paths for output.
4. If directory scanning reveals existing spec files, IGNORE them entirely — only process
   the files at the paths explicitly stated in the prompt.
