use anyhow::Result;
use std::path::{Path, PathBuf};

/// Check file access policy for an agent and print result.
/// Exits 1 if denied.
pub fn check(agent: &str, path: &str, project_root: &Path) -> Result<()> {
    let normalized = normalize_path(path, project_root);
    let normalized_str = normalized.to_string_lossy();

    // Immutable files — only Daniel edits these
    let immutable = ["constitution.md", "drifter.toml"];
    if immutable.iter().any(|f| normalized == Path::new(f)) {
        println!("DENIED: {} is immutable", normalized_str);
        std::process::exit(1);
    }

    // Agent isolation — agents/X/ writable only by agent X
    if normalized.starts_with("agents") {
        let parts: Vec<_> = normalized.iter().collect();
        if parts.len() >= 2 && parts[1] != agent {
            println!("DENIED: agent {} cannot access {}", agent, normalized_str);
            std::process::exit(1);
        }
    }

    println!("ALLOWED");
    Ok(())
}

fn normalize_path(path: &str, project_root: &Path) -> PathBuf {
    let input = Path::new(path);
    if input.is_absolute() {
        if let Ok(relative) = input.strip_prefix(project_root) {
            return relative.to_path_buf();
        }
        return input.to_path_buf();
    }

    input.to_path_buf()
}
