use anyhow::Result;
use std::path::Path;
use tokio::process::Command;

/// Extract the agent name from a branch like `agent/<name>/<topic>` or `agent/worktree/<name>`.
fn agent_from_branch(branch: &str) -> Option<&str> {
    let rest = branch.strip_prefix("agent/")?;
    let mut parts = rest.splitn(3, '/');
    let first = parts.next()?;
    if first == "worktree" {
        parts.next()
    } else {
        Some(first)
    }
}

/// Run the quality gate. Exits 0 on pass, 1 on fail.
pub async fn run(project_root: &Path, branch_override: Option<&str>) -> Result<()> {
    let mut changed = tracked_changes(project_root).await?;
    changed.extend(untracked_changes(project_root).await?);
    changed.sort();
    changed.dedup();

    if changed.is_empty() {
        println!("PASS: no changed files");
        return Ok(());
    }

    let mut passed = true;

    for immutable in ["constitution.md", "drifter.toml"] {
        if changed.iter().any(|path| path == immutable) {
            println!("FAIL: immutable file changed {}", immutable);
            passed = false;
        }
    }

    // Resolve branch: explicit override (from auto-merge) or detect from git
    let detected = current_branch(project_root).await.unwrap_or_default();
    let branch = branch_override.unwrap_or(&detected);

    // Agent sovereignty — agents cannot modify other agents' directories
    if let Some(branch_agent) = agent_from_branch(branch) {
        for f in &changed {
            if let Some(rest) = f.strip_prefix("agents/") {
                if let Some(file_agent) = rest.split('/').next() {
                    if !file_agent.is_empty() && file_agent != branch_agent {
                        println!(
                            "FAIL: agents cannot modify other agents' files: {}",
                            f
                        );
                        passed = false;
                    }
                }
            }
        }
    }

    // Path antipattern — gateways must not hardcode project root from __file__
    for f in &changed {
        if f.ends_with(".py") && (f.starts_with("gateways/") || f.starts_with("dashboard/")) {
            let full_path = project_root.join(f);
            if let Ok(content) = std::fs::read_to_string(&full_path) {
                if content.contains("Path(__file__).resolve().parent.parent") {
                    println!(
                        "FAIL: {} derives project_root from __file__ — accept it as a parameter or use resolve_project_root() from harness/common.py",
                        f
                    );
                    passed = false;
                }
            }
        }
    }

    // 1. Rust files → cargo check
    if changed
        .iter()
        .any(|f| f.ends_with(".rs") || f == "rust/Cargo.toml" || f.starts_with("rust/migrations/"))
    {
        print!("Checking Rust... ");
        let status = Command::new("cargo")
            .args(["check", "--workspace"])
            .current_dir(project_root.join("rust"))
            .status()
            .await;
        match status {
            Ok(s) if s.success() => println!("ok"),
            Ok(_) => { println!("FAIL"); passed = false; }
            Err(ref e) if e.kind() == std::io::ErrorKind::NotFound => {
                println!("skipped (cargo not on PATH)");
            }
            Err(e) => {
                println!("FAIL ({e})");
                passed = false;
            }
        }
    }

    // 2. Python files → py_compile
    for f in &changed {
        if f.ends_with(".py") {
            let status = Command::new("python3")
                .args(["-m", "py_compile", f])
                .current_dir(project_root)
                .status()
                .await?;
            if !status.success() {
                println!("FAIL: py_compile {}", f);
                passed = false;
            }
        }
    }

    // 3. harness/ and gateways/ Python → import check
    for f in &changed {
        if f.ends_with(".py") && (f.starts_with("harness/") || f.starts_with("gateways/")) {
            let module = python_module_from_path(f);
            let status = Command::new("python3")
                .args(["-c", &format!("import {}", module)])
                .current_dir(project_root)
                .status()
                .await?;
            if !status.success() {
                println!("FAIL: import {}", module);
                passed = false;
            }
        }
    }

     // 4. Run pytest if test files exist
     let test_dir = project_root.join("tests");
     if test_dir.is_dir() {
         let has_tests = std::fs::read_dir(&test_dir)?
             .filter_map(|e| e.ok())
             .any(|e| {
                 e.path()
                     .extension()
                     .map_or(false, |ext| ext == "py")
             });

         if has_tests {
             print!("Running tests... ");
             let drifter_bin = std::env::var("DRIFTER_BIN")
                 .ok()
                 .and_then(|path| {
                     if std::fs::metadata(&path).is_ok() {
                         Some(path)
                     } else {
                         None
                     }
                 })
                 .unwrap_or_else(|| {
                     project_root
                         .join("rust")
                         .join("target")
                         .join("release")
                         .join("drifter")
                         .to_string_lossy()
                         .to_string()
                 });
             let status = Command::new("python3")
                 .args(["-m", "pytest", "tests/", "-x", "--timeout=60", "-q"])
                 .current_dir(project_root)
                 .env("DRIFTER_BIN", &drifter_bin)
                 .status()
                 .await?;
             if status.success() {
                 println!("ok");
             } else {
                 println!("FAIL");
                 passed = false;
             }
         }
     }

    // 5. Migration immutability — modified (not created) migrations are rejected
    for f in modified_migrations(project_root).await? {
        println!("FAIL: modified existing migration {}", f);
        passed = false;
    }

    // 6. New migrations — agents cannot create database migrations (agent/* branches only)
    if branch.starts_with("agent/") {
        for f in new_migrations(project_root).await? {
            println!(
                "FAIL: agents cannot create database migrations — propose schema changes via the bus: {}",
                f
            );
            passed = false;
        }
    }

    if passed {
        println!("PASS");
    } else {
        println!("FAIL");
        std::process::exit(1);
    }

    Ok(())
}

async fn tracked_changes(project_root: &Path) -> Result<Vec<String>> {
    let output = Command::new("git")
        .args(["diff", "--name-only", "HEAD"])
        .current_dir(project_root)
        .output()
        .await?;

    Ok(lines(&output.stdout))
}

async fn untracked_changes(project_root: &Path) -> Result<Vec<String>> {
    let output = Command::new("git")
        .args(["ls-files", "--others", "--exclude-standard"])
        .current_dir(project_root)
        .output()
        .await?;

    Ok(lines(&output.stdout))
}

async fn new_migrations(project_root: &Path) -> Result<Vec<String>> {
    // Files added to rust/migrations/ (staged but not in HEAD)
    let output = Command::new("git")
        .args([
            "diff",
            "--diff-filter=A",
            "--name-only",
            "HEAD",
            "--",
            "rust/migrations/",
        ])
        .current_dir(project_root)
        .output()
        .await?;

    let mut result = lines(&output.stdout);

    // Also catch untracked migration files
    let untracked = Command::new("git")
        .args([
            "ls-files",
            "--others",
            "--exclude-standard",
            "--",
            "rust/migrations/",
        ])
        .current_dir(project_root)
        .output()
        .await?;

    result.extend(lines(&untracked.stdout));
    result.sort();
    result.dedup();
    Ok(result)
}

async fn current_branch(project_root: &Path) -> Result<String> {
    let output = Command::new("git")
        .args(["rev-parse", "--abbrev-ref", "HEAD"])
        .current_dir(project_root)
        .output()
        .await?;
    Ok(String::from_utf8_lossy(&output.stdout).trim().to_string())
}

async fn modified_migrations(project_root: &Path) -> Result<Vec<String>> {
    let output = Command::new("git")
        .args([
            "diff",
            "--diff-filter=M",
            "--name-only",
            "HEAD",
            "--",
            "rust/migrations/",
        ])
        .current_dir(project_root)
        .output()
        .await?;

    Ok(lines(&output.stdout))
}

fn lines(output: &[u8]) -> Vec<String> {
    String::from_utf8_lossy(output)
        .lines()
        .filter(|line| !line.is_empty())
        .map(str::to_string)
        .collect()
}

fn python_module_from_path(path: &str) -> String {
    let module = path.trim_end_matches(".py");
    if let Some(package) = module.strip_suffix("/__init__") {
        package.replace('/', ".")
    } else {
        module.replace('/', ".")
    }
}
