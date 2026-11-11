/**
 * Snow-physics (Physics menu) selection: user preference vs effective method.
 *
 * The Physics selection is a durable user preference, not a per-location
 * setting (user directive, 2026-11-11):
 *
 * - With no stored preference, the selection defaults to the ENS-EB model.
 * - The preference is persisted in localStorage (key
 *   PHYSICS_METHOD_STORAGE_KEY, the plain option id -- the same shape as
 *   the elevation-mode preference, 'mysnow_elevation_mode') and survives
 *   location switches and page reloads. Switching locations NEVER rewrites
 *   it. (Before this change the selection lived only in page state,
 *   initialised to Kuchera, and an effect in page.tsx actively reverted an
 *   ENS-EB selection to Kuchera whenever the current location or forecast
 *   mode could not serve the model -- so one trip to a non-catalogue
 *   location permanently destroyed the user's selection.)
 * - Where the preferred method cannot apply, the preference stays and the
 *   EFFECTIVE method degrades gracefully: an ENS-EB preference outside its
 *   serving context (non-catalogue location, or a forecast-source mode
 *   other than ECMWF / HRRR / the hrrr_ecmwf blend) computes with Kuchera
 *   -- which is what the ENS-EB path uses as its substrate anyway
 *   (toSlrMethod maps the model option to Kuchera, and
 *   applyModelMethodToDays returns the days unchanged when the method is
 *   unavailable). Returning to a catalogue point in a valid mode makes
 *   ENS-EB effective again with no re-selection. Every other method is
 *   always effective exactly as selected.
 */
import { useEffect, useState } from 'react';
import { ALGORITHMS } from './constants';
import {
  ENS_EB_METHOD_ID,
  type ModelMethodAvailability,
  type ModelMethodUnavailableReason,
} from './model-forecast';
import { DEFAULT_SLR_METHOD } from './snow/types';

/** localStorage key for the Physics preference (plain method id string). */
export const PHYSICS_METHOD_STORAGE_KEY = 'mysnow_slr_method';

/** Physics selection when no (valid) preference has been stored. */
export const DEFAULT_PHYSICS_METHOD_ID: string = ENS_EB_METHOD_ID;

/**
 * Method that computes in place of an ENS-EB preference where the model
 * cannot serve. Identical to the ENS-EB path's own substrate, so the
 * degradation changes no number the fallback labelling does not describe.
 */
export const MODEL_FALLBACK_METHOD_ID: string = DEFAULT_SLR_METHOD;

/** Minimal storage surface, satisfied by the DOM localStorage. */
export interface PhysicsPreferenceStorage {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
}

/** True when the id is one of the Physics menu options. */
export function isKnownPhysicsMethodId(id: string | null | undefined): boolean {
  return id != null && ALGORITHMS.some((opt) => opt.id === id);
}

/**
 * Interpret a stored value: a known Physics option id is honoured as-is;
 * anything else (absent, stale, hand-edited) counts as "no stored
 * preference" and yields the default.
 */
export function parsePhysicsPreference(raw: string | null | undefined): string {
  return isKnownPhysicsMethodId(raw) ? (raw as string) : DEFAULT_PHYSICS_METHOD_ID;
}

/** Read the persisted Physics preference from storage. */
export function loadPhysicsPreference(storage: PhysicsPreferenceStorage): string {
  return parsePhysicsPreference(storage.getItem(PHYSICS_METHOD_STORAGE_KEY));
}

/**
 * Persist a Physics preference. Unknown ids are refused (returns false)
 * so a corrupt value can never displace a valid stored preference.
 */
export function savePhysicsPreference(
  storage: PhysicsPreferenceStorage,
  methodId: string,
): boolean {
  if (!isKnownPhysicsMethodId(methodId)) return false;
  storage.setItem(PHYSICS_METHOD_STORAGE_KEY, methodId);
  return true;
}

export interface EffectivePhysicsMethod {
  /** The user's stored/selected preference -- never rewritten by context. */
  preferenceId: string;
  /** The method whose numbers are actually computed right now. */
  effectiveId: string;
  /**
   * True when the context PERMITS ENS-EB (catalogue point + serving
   * mode). This is a context statement, not an execution claim: within a
   * permitted context, individual windows without a model record still
   * compute with Kuchera, and that per-window execution status is carried
   * by the days' modelWindow marks ('model' vs 'kuchera_fallback'), not
   * by this flag.
   */
  modelActive: boolean;
  /** True when the preference is ENS-EB but Kuchera is computing instead. */
  degraded: boolean;
  /** Why ENS-EB cannot serve in the current context, when degraded. */
  reason: ModelMethodUnavailableReason | null;
}

/**
 * Resolve preference + serving context to the effective method. Pure: the
 * preference is an input, never an output -- resolving effectiveness must
 * not mutate the selection (that mutation was the pre-2026-11-11 bug).
 */
export function resolveEffectivePhysicsMethod(
  preferenceId: string,
  availability: ModelMethodAvailability,
): EffectivePhysicsMethod {
  if (preferenceId !== ENS_EB_METHOD_ID) {
    return {
      preferenceId,
      effectiveId: preferenceId,
      modelActive: false,
      degraded: false,
      reason: null,
    };
  }
  if (availability.available) {
    return {
      preferenceId,
      effectiveId: ENS_EB_METHOD_ID,
      modelActive: true,
      degraded: false,
      reason: null,
    };
  }
  return {
    preferenceId,
    effectiveId: MODEL_FALLBACK_METHOD_ID,
    modelActive: false,
    degraded: true,
    reason: availability.reason,
  };
}

export interface PhysicsPreferenceHook {
  /** The current Physics preference (the default until restoration lands). */
  preferenceId: string;
  /** Explicit user selection -- the only sanctioned way to change it. */
  setPreferenceId: (id: string) => void;
  /**
   * True once the mount-time restoration has committed. Consumers that
   * must not act on the pre-restoration default (initial data fetches)
   * gate on this.
   */
  restored: boolean;
}

/**
 * React wiring for the Physics preference, used by the app page.
 *
 * Restoration and persistence are ordered by a STATE flag, on purpose
 * (Codex gate, 2026-11-11): the restore effect only QUEUES the restored
 * value, so a persist effect keyed on the preference alone would, in the
 * same mount pass, write the pre-restoration default over a valid stored
 * preference -- and under Strict Mode's development effect replay the
 * restore effect would then read that clobbered value back and the
 * preference would be lost outright. Gating persistence on `restored`
 * (set in the same commit as the restored preference) means storage is
 * only ever written with a value the user actually holds: initially the
 * restored value itself, thereafter their explicit selections.
 */
export function usePhysicsPreference(): PhysicsPreferenceHook {
  const [preferenceId, setPreferenceId] = useState<string>(DEFAULT_PHYSICS_METHOD_ID);
  const [restored, setRestored] = useState(false);

  useEffect(() => {
    setPreferenceId(loadPhysicsPreference(window.localStorage));
    setRestored(true);
  }, []);

  useEffect(() => {
    if (!restored) return;
    savePhysicsPreference(window.localStorage, preferenceId);
  }, [restored, preferenceId]);

  return { preferenceId, setPreferenceId, restored };
}
