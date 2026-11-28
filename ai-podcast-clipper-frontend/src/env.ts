// Server-side settings from the environment. Both are optional: the defaults work when
// the repo layout is unchanged (see .env.example). An empty string counts as unset.

const unset = (value: string | undefined) => (value === "" ? undefined : value);

export const env = {
  /** Data folder (jobs, stats, settings). Default: %LOCALAPPDATA%\ai-podcast-clipper\data (see lib/paths.ts) */
  DATA_DIR: unset(process.env.DATA_DIR),
  /** Python interpreter that starts runner.py. Default: the backend's .venv. */
  RUNNER_PYTHON: unset(process.env.RUNNER_PYTHON),
};
