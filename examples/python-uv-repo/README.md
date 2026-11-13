# Python uv repository scaffold

Copy these files into a Python repository that uses uv, then follow `docs/python-ci.md`:

| File | Goes to | Notes |
|---|---|---|
| `.github/workflows/ci.yml` | same path | Replace both `<commit-sha>` placeholders with one reviewed commit SHA |
| `.pre-commit-config.yaml` | same path | Has the vendor-exclude slot at the top |
| `ruff-base.toml` | same path | Byte-identical copy of `configs/ruff-base.toml`. Do not edit it |
| `ruff.toml` | same path | Your rules; extends the base |

`ruff-base.toml` and `.pre-commit-config.yaml` are copies of the files in `configs/`; a test keeps them equal.
