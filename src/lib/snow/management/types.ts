export type ManagementEvent =
  | {
      type: 'snowmaking';
      start: string;
      end: string;
      waterFlowM3h: number;
      expectedDensityKgM3: number;
      affectedUnits: string[];
    }
  | {
      type: 'grooming';
      time: string;
      compactionFraction: number;
      affectedUnits: string[];
    }
  | {
      type: 'snow_transfer';
      time: string;
      fromUnit: string;
      toUnit: string;
      sweKg: number;
    }
  | {
      type: 'closure' | 'opening';
      time: string;
      affectedUnits: string[];
    };

export interface ManagedSnowState {
  unitId: string;
  areaM2: number;
  sweMm: number;
  depthCm: number;
  densityKgM3: number;
  isOpen: boolean;
}
