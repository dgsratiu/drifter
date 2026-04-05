use anyhow::Result;
use std::path::Path;
use tokio::process::Command;

/// Run the quality gate. Exits 0 on pass, 1 on fail.
pub async fn run(project_root: &Path) -> Result<()> {
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
            let drifter_bin = std::env::var("DRIFTER_BIN").unwrap_or_else(|_| {
                project_root
                    .join("rust")
                    .join("target")
                    .join("debug")
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
