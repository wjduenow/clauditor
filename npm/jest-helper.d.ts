// TypeScript declarations for clauditor-eval's `toPassClauditor` matcher.
//
//   import { toPassClauditor } from "clauditor-eval/jest-helper";
//   expect.extend({ toPassClauditor });
//   await expect(result).toPassClauditor(evalPath);
//
// Works under both Jest and Vitest. The matcher is async — always `await`
// the assertion.

/**
 * The raw matcher function passed to `expect.extend({ toPassClauditor })`.
 *
 * `received` is either a `validate()` result (used directly) or a
 * `runSkill()` result (in which case `evalPath` is required). Returns a
 * Promise resolving to the standard matcher result.
 */
export function toPassClauditor(
  this: { isNot?: boolean } | void,
  received: unknown,
  evalPath?: string
): Promise<{ pass: boolean; message: () => string }>;

// Best-effort matcher-type augmentation for both Jest and Vitest. These
// module augmentations are guarded by `declare global` so they only take
// effect when the host project already pulls in the runner's type
// definitions; if a runner's namespace is absent, its block is inert.

declare global {
  // eslint-disable-next-line @typescript-eslint/no-namespace
  namespace jest {
    interface Matchers<R> {
      /**
       * Assert a clauditor eval passed. Pass an `evalPath` when the
       * received value is a `runSkill()` result. Async — `await` it.
       */
      toPassClauditor(evalPath?: string): Promise<R>;
    }
  }
}

declare module "vitest" {
  interface Assertion<T = unknown> {
    /**
     * Assert a clauditor eval passed. Pass an `evalPath` when the
     * received value is a `runSkill()` result. Async — `await` it.
     */
    toPassClauditor(evalPath?: string): Promise<T>;
  }
  interface AsymmetricMatchersContaining {
    toPassClauditor(evalPath?: string): unknown;
  }
}

export {};
