import { describe, expect, it } from 'vitest';
import { ALGORITHMS } from '../constants';
import {
  BLEND_MODEL_ID,
  ENS_EB_METHOD_ID,
  modelMethodAvailability,
} from '../model-forecast';
import {
  DEFAULT_PHYSICS_METHOD_ID,
  MODEL_FALLBACK_METHOD_ID,
  PHYSICS_METHOD_STORAGE_KEY,
  isKnownPhysicsMethodId,
  loadPhysicsPreference,
  parsePhysicsPreference,
  resolveEffectivePhysicsMethod,
  savePhysicsPreference,
  type PhysicsPreferenceStorage,
} from '../physics-preference';
import type { Location } from '../types';

class FakeStorage implements PhysicsPreferenceStorage {
  private map = new Map<string, string>();
  getItem(key: string): string | null {
    return this.map.has(key) ? (this.map.get(key) as string) : null;
  }
  setItem(key: string, value: string): void {
    this.map.set(key, value);
  }
}

const palisades: Location = {
  id: 'palisades',
  name: 'Palisades Tahoe',
  latitude: 39.193416,
  longitude: -120.245402,
  elevationFt: 8200,
  elevationM: 2499,
  minElevationM: 1889,
  maxElevationM: 2708,
  referencePoints: [
    { id: 'palisades-base', name: 'Village base', role: 'base', latitude: 39.196999, longitude: -120.24313, elevationM: 1889, geometrySource: 'curated' },
    { id: 'palisades-high-camp', name: 'High Camp', role: 'mid_mountain', latitude: 39.195899, longitude: -120.260845, elevationM: 2499, geometrySource: 'curated' },
    { id: 'palisades-upper-mountain', name: 'Upper mountain', role: 'summit', latitude: 39.181764, longitude: -120.266494, elevationM: 2708, geometrySource: 'curated' },
  ],
  timezone: 'America/Los_Angeles',
  isCustom: false,
} as Location;

const farAway: Location = {
  id: 'nowhere',
  name: 'Nowhere',
  latitude: 30.0,
  longitude: -90.0,
  elevationFt: 300,
  elevationM: 100,
  minElevationM: 90,
  maxElevationM: 110,
  timezone: 'America/Chicago',
  isCustom: true,
} as Location;

describe('physics preference defaults and storage', () => {
  it('(a) fresh state: no stored preference selects ENS-EB by default', () => {
    const storage = new FakeStorage();
    expect(loadPhysicsPreference(storage)).toBe(ENS_EB_METHOD_ID);
    expect(DEFAULT_PHYSICS_METHOD_ID).toBe(ENS_EB_METHOD_ID);
    expect(isKnownPhysicsMethodId(DEFAULT_PHYSICS_METHOD_ID)).toBe(true);
  });

  it('stores the preference as the plain option id under the mysnow key', () => {
    expect(PHYSICS_METHOD_STORAGE_KEY).toBe('mysnow_slr_method');
    const storage = new FakeStorage();
    expect(savePhysicsPreference(storage, 'cobb_2011')).toBe(true);
    expect(storage.getItem(PHYSICS_METHOD_STORAGE_KEY)).toBe('cobb_2011');
  });

  it('(b) a stored non-default preference is honoured on load', () => {
    const storage = new FakeStorage();
    savePhysicsPreference(storage, 'cobb_2011');
    expect(loadPhysicsPreference(storage)).toBe('cobb_2011');
    savePhysicsPreference(storage, 'kuchera');
    expect(loadPhysicsPreference(storage)).toBe('kuchera');
    savePhysicsPreference(storage, ENS_EB_METHOD_ID);
    expect(loadPhysicsPreference(storage)).toBe(ENS_EB_METHOD_ID);
  });

  it('treats absent or unrecognised stored values as no preference', () => {
    expect(parsePhysicsPreference(null)).toBe(ENS_EB_METHOD_ID);
    expect(parsePhysicsPreference(undefined)).toBe(ENS_EB_METHOD_ID);
    expect(parsePhysicsPreference('not-a-method')).toBe(ENS_EB_METHOD_ID);
    expect(parsePhysicsPreference('')).toBe(ENS_EB_METHOD_ID);
  });

  it('refuses to persist an unknown method id', () => {
    const storage = new FakeStorage();
    savePhysicsPreference(storage, 'fixed_10');
    expect(savePhysicsPreference(storage, 'bogus')).toBe(false);
    expect(loadPhysicsPreference(storage)).toBe('fixed_10');
  });

  it('every Physics menu option id is a valid preference value', () => {
    for (const opt of ALGORITHMS) {
      expect(isKnownPhysicsMethodId(opt.id)).toBe(true);
      expect(parsePhysicsPreference(opt.id)).toBe(opt.id);
    }
    expect(isKnownPhysicsMethodId(null)).toBe(false);
    expect(isKnownPhysicsMethodId('nope')).toBe(false);
  });
});

describe('preference vs effective method across location switches', () => {
  it('(c) catalogue -> non-catalogue -> catalogue keeps the ENS-EB preference, Kuchera effective in the middle', () => {
    const preference = loadPhysicsPreference(new FakeStorage());
    expect(preference).toBe(ENS_EB_METHOD_ID);

    const atCatalogue = resolveEffectivePhysicsMethod(
      preference,
      modelMethodAvailability(BLEND_MODEL_ID, palisades, 'min'),
    );
    expect(atCatalogue.preferenceId).toBe(ENS_EB_METHOD_ID);
    expect(atCatalogue.effectiveId).toBe(ENS_EB_METHOD_ID);
    expect(atCatalogue.modelActive).toBe(true);
    expect(atCatalogue.degraded).toBe(false);

    const away = resolveEffectivePhysicsMethod(
      preference,
      modelMethodAvailability(BLEND_MODEL_ID, farAway, 'min'),
    );
    // The preference is untouched by the switch; only effectiveness moves.
    expect(away.preferenceId).toBe(ENS_EB_METHOD_ID);
    expect(away.effectiveId).toBe(MODEL_FALLBACK_METHOD_ID);
    expect(away.effectiveId).toBe('kuchera');
    expect(away.modelActive).toBe(false);
    expect(away.degraded).toBe(true);
    expect(away.reason).toBe('non-catalogue-location');

    const backAgain = resolveEffectivePhysicsMethod(
      preference,
      modelMethodAvailability(BLEND_MODEL_ID, palisades, 'min'),
    );
    expect(backAgain.preferenceId).toBe(ENS_EB_METHOD_ID);
    expect(backAgain.effectiveId).toBe(ENS_EB_METHOD_ID);
    expect(backAgain.modelActive).toBe(true);
    expect(backAgain.degraded).toBe(false);
  });

  it('(c) an unsupported forecast mode degrades the same way, and a valid mode restores ENS-EB', () => {
    const preference = ENS_EB_METHOD_ID;
    const inGfs = resolveEffectivePhysicsMethod(
      preference,
      modelMethodAvailability('gfs', palisades, 'min'),
    );
    expect(inGfs.preferenceId).toBe(ENS_EB_METHOD_ID);
    expect(inGfs.effectiveId).toBe('kuchera');
    expect(inGfs.degraded).toBe(true);
    expect(inGfs.reason).toBe('unsupported-mode');

    for (const mode of ['ecmwf', 'hrrr', BLEND_MODEL_ID]) {
      const restored = resolveEffectivePhysicsMethod(
        preference,
        modelMethodAvailability(mode, palisades, 'min'),
      );
      expect(restored.modelActive).toBe(true);
      expect(restored.effectiveId).toBe(ENS_EB_METHOD_ID);
    }
  });

  it('(d) a non-ENS-EB choice persists across reload and is effective at any location', () => {
    const storage = new FakeStorage();
    savePhysicsPreference(storage, 'cobb_2011');

    // Simulated reload: the preference is read back from storage.
    const reloaded = loadPhysicsPreference(storage);
    expect(reloaded).toBe('cobb_2011');

    // Location switches do not degrade or rewrite a non-model method.
    for (const loc of [palisades, farAway]) {
      const resolved = resolveEffectivePhysicsMethod(
        reloaded,
        modelMethodAvailability(BLEND_MODEL_ID, loc, 'min'),
      );
      expect(resolved.preferenceId).toBe('cobb_2011');
      expect(resolved.effectiveId).toBe('cobb_2011');
      expect(resolved.degraded).toBe(false);
      expect(resolved.modelActive).toBe(false);
    }
    // Nothing in the round trip rewrote the stored value.
    expect(loadPhysicsPreference(storage)).toBe('cobb_2011');
  });
});
