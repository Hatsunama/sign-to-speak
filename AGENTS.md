# Repository development

The active app is the React Native Android project in `native/`. Read `native/README.md` and the relevant `native/docs/` and `native/scripts/CI.md` guidance before changing or verifying it. Root-level browser prototype files were retired; use Git history for their provenance. Preserve `qa-artifacts/` evidence and all signing credentials.

On the owner’s Windows host, use `C:/sign_to_speak/repository` as the active checkout and keep this project’s evidence, models, scratch files and temporary test directories under `C:/sign_to_speak`. The old OneDrive and xMilo paths are retired. Keep existing signing credentials and shared SDK/JDK installations in their protected locations; never move or upload a key to satisfy workspace consolidation. Use hosted CI for heavy builds and preserve the owner’s device/resource coordination rules in `native/docs/COMMERCIAL_READINESS_PLAN.md`.
