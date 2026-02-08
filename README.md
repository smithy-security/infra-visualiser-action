# Infra Visualiser Action

A GitHub Action that analyzes Terraform/OpenTofu recipes, generates graphs and schemas, and uploads them to a platform.

## Features

- Automatically discovers `.tfvars` files in your repository
- Runs OpenTofu plans with multiple variable files until one succeeds
- Generates Terraform graphs and provider schemas
- Finds and includes local Terraform modules
- Uploads artifacts to your platform using OIDC authentication

## Usage

### Basic Example

```yaml
name: Visualize Infrastructure

on:
  push:
    branches: [main]
    paths:
      - 'terraform/**'
  workflow_dispatch:

jobs:
  visualize:
    runs-on: ubuntu-latest
    permissions:
      id-token: write  # Required for OIDC token
      contents: read   # Required to read repository files
    
    steps:
      - uses: actions/checkout@v4
      
      - name: Visualize Terraform Recipe
        uses: your-org/visualiser-action@v1
        with:
          directory: "terraform/my-recipe"
          recipe-nickname: "my-recipe"
          host: "https://platform.example.com"  # Optional, has default
```

### Uploading to GitHub Artifacts

To enable uploading the generated archive as a GitHub Artifact, you must enable `upload_to_github` and provide the `github_token`. 

Additionally, you must expose the `ACTIONS_RUNTIME_TOKEN` and `ACTIONS_RUNTIME_URL` environment variables. These are not automatically passed to Docker actions, so you need a step using `actions/github-script` to expose them.

```yaml
jobs:
  visualize:
    runs-on: ubuntu-latest
    permissions:
      id-token: write
      contents: read
      actions: read    # Required for listing/checking artifacts
    
    steps:
      - uses: actions/checkout@v4

      # REQUIRED: Expose runtime variables for artifact upload
      - name: Expose Actions Runtime
        uses: actions/github-script@v7
        with:
          script: |
            core.exportVariable('ACTIONS_RUNTIME_TOKEN', process.env.ACTIONS_RUNTIME_TOKEN)
            core.exportVariable('ACTIONS_RUNTIME_URL', process.env.ACTIONS_RUNTIME_URL)

      - name: Visualize Terraform Recipe
        uses: your-org/visualiser-action@v1
        with:
          directory: "terraform/my-recipe"
          recipe-nickname: "my-recipe"
          upload_to_github: "true"
          github_token: ${{ secrets.GITHUB_TOKEN }}
```

### Inputs

| Input | Description | Required | Default |
|-------|-------------|----------|---------|
| `directory` | Path (relative to repo root) to the Terraform/OpenTofu recipe | Yes | - |
| `recipe-nickname` | Human-friendly nickname for this recipe | Yes | - |
| `host` | Base URL of the platform host (used as OIDC audience and upload target) | No | `https://infra-app-916613094271.europe-west1.run.app` |
| `upload_to_github` | Upload the generated archive as a GitHub Artifact | No | `false` |
| `github_token` | GitHub Token (required if upload_to_github is true) | No | - |

### Permissions

This action requires the following permissions:

- `id-token: write` - To obtain OIDC token from GitHub
- `contents: read` - To read repository files and Terraform code
- `actions: read` - (Optional) To list artifacts when `upload_to_github` is enabled

### How It Works

1. **Discovery**: Finds all `.tfvars` files in your repository
2. **Planning**: Attempts to run `tofu plan` with:
   - Default variables first
   - Each `.tfvars` file found (stops on first success)
3. **Generation**: Creates:
   - Terraform graph (`.dot` file)
   - Provider schema (JSON)
   - Plan output (JSON)
4. **Module Detection**: Finds local Terraform modules from `modules.json`
5. **Archive**: Packages all artifacts into a tarball
6. **Upload**: Uploads to your platform using OIDC authentication

### Requirements

- OpenTofu must be available (the action includes it)
- Your repository must contain Terraform/OpenTofu code
- The platform host must accept OIDC tokens

## Versioning

This action uses semantic versioning. You can reference specific versions:

- `@v1` - Latest v1.x.x release
- `@v1.0.0` - Specific version
- `@main` - Latest from main branch (not recommended for production)
