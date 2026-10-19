I couldn't dedupe the file because `customers_export.csv` isn't in the working directory (`/private/var/folders/1y/7cgp6w0n4p10n9cqyplr22dw0000gn/T/skill-eval-claude-moji2bno/work`). The cleaner script failed with "input must be a regular workdir file without aliases". Reading the path directly gave "file does not exist". I can't list the directory in this environment, so I can't see where the file is.

Could you tell me where the file actually is? It needs to be a regular file in this directory, not a symlink or alias. If it's somewhere else, copy it here or give me the full path.

Once it's in place, I'll write the result to a new file, `customers_export.clean.csv`. The original won't be touched.

The cleaner also trims leading and trailing whitespace from every cell, not just removing duplicates. It trims first, so rows that differ only by stray spaces will also count as duplicates. If you need the whitespace left as it is, tell me before I run it.