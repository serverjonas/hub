# Contributing Guide

Thank you for contributing to this project. This document defines the rules and standards for all contributions to keep the project consistent, stable, and maintainable.

---

## 1. Core Principles

- Every change must be clear, intentional, and documented.
- Code should be readable, structured, and maintainable.
- No undocumented or “quick fix” changes.
- Keep changes small and logically separated when possible.

---

## 2. Changelog Requirement

Every change must be recorded in the `CHANGELOG.md`.

### Rules:
- Each change must have its own entry
- Include the date of the change
- Briefly describe what was changed
- Optionally include the reason for the change

### Example:
[2026-07-03]
- Fix: Fixed login failure when password is empty
- Add: New API endpoint for user data
- Change: Refactored configuration structure

---

## 3. Commit Guidelines

- Commit messages must be clear and meaningful
- Avoid generic messages like "fix" or "update"
- Always describe what was changed

### Examples:
- `Fix: Crash when loading configuration`
- `Add: Server status endpoint`
- `Refactor: Simplify networking module`

---

## 4. Code Structure

- Keep code clean and organized
- Avoid duplication whenever possible
- Functions should do one thing only
- Use meaningful file and variable names

---

## 5. Documentation

- New features must be documented
- Changes to existing behavior must update documentation
- Complex logic should include a short explanation

---

## 6. Project Structure Rules

- Do not create random or unnecessary folders
- New modules must follow the existing architecture
- Do not restructure the project without a clear reason or agreement

---

## 7. Testing & Stability

- Changes must not break existing functionality
- Test changes before submitting whenever possible
- Bugs must be reproducible and not ignored

---

## 8. Naming Conventions

- Use clear and consistent naming
- Files: follow the existing convention (`snake_case` or `kebab-case`)
- Variables: descriptive names, avoid unnecessary abbreviations

---

## 9. Pull Requests

- Keep pull requests focused on a single topic
- Describe clearly what the PR changes
- Reference related issues if applicable
- Ensure the code is clean before requesting review

---

## 10. General Rules

- No untested code in main branches
- No unfinished features without clear indication
- Clean up unused or dead code after changes
- Always prioritize system stability and clarity

---

## 11. Project Goal

The goal of this project is to remain stable, modular, and easy to extend over time. Every contribution should support this goal.
