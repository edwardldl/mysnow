// @vitest-environment jsdom
/**
 * Component-level regression tests for usePhysicsPreference under
 * StrictMode (Codex gate, 2026-11-11). The defect class pinned here is
 * mount-ordering: a persist effect that runs before the restored
 * preference commits writes the pre-restoration default over a valid
 * stored preference, and StrictMode's development effect replay then
 * reads the clobbered value back into state. The pure-helper tests in
 * physics-preference.test.ts cannot see this lifecycle, so the hook is
 * mounted for real, under StrictMode, against the real localStorage.
 */
import { StrictMode, act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { ENS_EB_METHOD_ID } from '../model-forecast';
import {
  PHYSICS_METHOD_STORAGE_KEY,
  usePhysicsPreference,
  type PhysicsPreferenceHook,
} from '../physics-preference';

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let root: Root | null = null;
let container: HTMLDivElement | null = null;
let latest: PhysicsPreferenceHook | null = null;

function Probe(): null {
  latest = usePhysicsPreference();
  return null;
}

async function mount(): Promise<void> {
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  await act(async () => {
    root?.render(createElement(StrictMode, null, createElement(Probe)));
  });
}

beforeEach(() => {
  window.localStorage.clear();
  latest = null;
});

afterEach(async () => {
  await act(async () => {
    root?.unmount();
  });
  root = null;
  container?.remove();
  container = null;
  window.localStorage.clear();
});

describe('usePhysicsPreference under StrictMode', () => {
  it('a stored Cobb preference survives the mount intact (selection and storage)', async () => {
    window.localStorage.setItem(PHYSICS_METHOD_STORAGE_KEY, 'cobb_2011');
    await mount();
    expect(latest?.restored).toBe(true);
    expect(latest?.preferenceId).toBe('cobb_2011');
    // The stored value must never have been overwritten by the
    // pre-restoration ENS-EB default along the way.
    expect(window.localStorage.getItem(PHYSICS_METHOD_STORAGE_KEY)).toBe('cobb_2011');
  });

  it('fresh storage restores the ENS-EB default', async () => {
    await mount();
    expect(latest?.restored).toBe(true);
    expect(latest?.preferenceId).toBe(ENS_EB_METHOD_ID);
  });

  it('an unrecognised stored value restores the ENS-EB default', async () => {
    window.localStorage.setItem(PHYSICS_METHOD_STORAGE_KEY, 'long-gone-method');
    await mount();
    expect(latest?.preferenceId).toBe(ENS_EB_METHOD_ID);
  });

  it('an explicit selection after restoration is what gets persisted', async () => {
    window.localStorage.setItem(PHYSICS_METHOD_STORAGE_KEY, 'cobb_2011');
    await mount();
    await act(async () => {
      latest?.setPreferenceId('fixed_10');
    });
    expect(latest?.preferenceId).toBe('fixed_10');
    expect(window.localStorage.getItem(PHYSICS_METHOD_STORAGE_KEY)).toBe('fixed_10');
  });
});
