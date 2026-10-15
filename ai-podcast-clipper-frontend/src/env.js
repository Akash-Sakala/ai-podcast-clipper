import { createEnv } from "@t3-oss/env-nextjs";
import { z } from "zod";

export const env = createEnv({
  /**
   * Server-side environment variables. Both are optional - the defaults work
   * when the repo layout is unchanged (see .env.example).
   */
  server: {
    NODE_ENV: z
      .enum(["development", "test", "production"])
      .default("development"),
    // Folder that holds one sub-folder per job (input video, clips, job.json).
    // Default: <repo>/data
    DATA_DIR: z.string().optional(),
    // Python interpreter used to start runner.py.
    // Default: <repo>/ai-podcast-clipper-backend/.venv/Scripts/python.exe (Windows)
    RUNNER_PYTHON: z.string().optional(),
  },

  client: {},

  runtimeEnv: {
    NODE_ENV: process.env.NODE_ENV,
    DATA_DIR: process.env.DATA_DIR,
    RUNNER_PYTHON: process.env.RUNNER_PYTHON,
  },
  /**
   * Run `build` or `dev` with `SKIP_ENV_VALIDATION` to skip env validation.
   */
  skipValidation: !!process.env.SKIP_ENV_VALIDATION,
  /**
   * Makes it so that empty strings are treated as undefined.
   */
  emptyStringAsUndefined: true,
});
