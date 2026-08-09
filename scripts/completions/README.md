# REGDOCS Atlas Bash completion

Enable completion once from the repository root:

```bash
source scripts/completions/install-bash.sh
```

That does two things:

1. enables completion in the current Bash shell;
2. adds an idempotent source line to `~/.bashrc` so future Bash shells load it automatically.

Examples after installation:

```text
python pipeline.py down<TAB>                 -> download
python pipeline.py download r<TAB>           -> run
python pipeline.py analyze az<TAB>           -> azure
python pipeline.py analyze azure p<TAB>      -> plan
python pipeline.py normalize run --pro<TAB>  -> --provider
python pipeline.py normalize run --provider a<TAB> -> azure
```

The completion hook is intentionally opt-in because supporting the exact `python pipeline.py ...` form requires registering a Bash completion function for `python` and `python3`. For non-REGDOCS Python commands the function returns control to Bash/default filename completion.

To disable it for the current shell:

```bash
complete -r python python3 pipeline.py ./pipeline.py
```

Remove the `# REGDOCS Atlas CLI completion` block from `~/.bashrc` if you also want to disable it for future shells.
