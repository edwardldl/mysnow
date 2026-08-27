import diagnosticsJson from "../../data/slr-methodology-diagnostics/diagnostics.json";

export type RegionId = "rockies" | "sierra" | "swiss" | "northeast";
export type MethodId = "baseline" | "globalPrior" | "intercept" | "ridgeThermal" | "ridgeFull";
export type MetricKey = "mae" | "rmse" | "bias";
export type Interval95 = readonly [number, number];

export interface Metrics {
  mae: number;
  rmse: number;
  bias: number;
}

interface RawSlice {
  eventCount: number;
  metrics: Record<MethodId, Metrics>;
}

interface RawRegionResult {
  evaluationCount: number;
  metrics: Record<MethodId, Metrics>;
  clusterBootstrap95: {
    station: { clusterCount: number; repetitions: number; intervals95: Record<string, [number, number]> };
    storm: { clusterCount: number; repetitions: number; intervals95: Record<string, [number, number]> };
  };
  slices: {
    byDimension: Record<string, Record<string, RawSlice>>;
    improvedSliceCounts: Record<string, Partial<Record<MethodId, { improved: number; total: number }>>>;
  };
}

interface RawDiagnostics {
  schemaVersion: number;
  status: string;
  purpose: string;
  productionEnablement: boolean;
  artifactProduced: boolean;
  limitations: string[];
  balancingDefinitions: Record<string, string>;
  determinism: {
    bootstrapSeedPolicy: string;
    selectionRegions: RegionId[];
    stdlibOnly: boolean;
    untouchedRegion: RegionId;
  };
  predictors: {
    allAvailableFeatureNames: string[];
    thermalAblationDefinition: string;
    fullFeatureDefinition: string;
    note: string;
  };
  fixedHoldoutEvaluation: {
    policy: string;
    results: Record<RegionId, RawRegionResult>;
  };
  provenanceAndSplitAssertions: Record<RegionId, {
    baselinePath: string;
    baselineSha256: string;
    profilesPath: string;
    profilesSha256: string;
    reportPath: string;
    reportSha256: string;
    temporalConstraint: Record<string, unknown>;
    trainEventCount: number;
    testEventCount: number;
    trainStations: string[];
    testStations: string[];
    trainStormCount: number;
    testStormCount: number;
    existingEvaluationHierarchyCounts: Record<string, number>;
  }>;
  trainingGroupedCvSelection: {
    objective: string;
    holdoutIsolation: string;
    foldFamilies: Record<string, string>;
    selected: Record<"intercept" | "ridgeThermal" | "ridgeFull", {
      balance: string;
      shrinkage?: number;
      alpha?: number;
      featureSet?: string;
      macroCvMae: number;
    }>;
  };
  globalPriorLeaveOneRegionOut: {
    decision: string;
    method: string;
    developmentLeaveOneRegionOut: Record<"rockies" | "sierra" | "swiss", RawPrior>;
    northeastFinal: RawPrior & { conclusion: string };
  };
  northeastUntouchedEvaluation: {
    evaluationPolicy: string;
    evaluationCount: number;
    metrics: Record<"baseline" | "intercept" | "ridgeThermal" | "ridgeFull", Metrics>;
    models: {
      intercept: { effectiveSampleSize: number; offset: number };
      ridgeThermal: { effectiveSampleSize: number; featureCount: number; featureNames: string[] };
      ridgeFull: { effectiveSampleSize: number; featureCount: number; featureNames: string[] };
    };
    rootCauseDiagnostic: {
      interpretation: string;
      existingEvaluationHierarchyCounts: Record<string, number>;
      trainLogObservedOverBaseline: ResidualSummary;
      testLogObservedOverBaseline: ResidualSummary;
    };
    empiricalGroupedCrossfitIntervals80: {
      status: string;
      warning: string;
      methods: Record<"intercept" | "ridgeThermal" | "ridgeFull", Record<"leaveStationOut" | "leaveStormOut", EmpiricalInterval>>;
    };
  };
  usLabelProvenanceWarning: {
    status: string;
    warning: string;
    strictBroadUsAudit: string;
    regions: Record<string, {
      selectedLabelCount: number;
      selectedSplitPath: string;
      selectedSplitSha256: string;
      sourceFlagCounts: Record<string, number>;
    }>;
  };
}

interface RawPrior {
  fitRegions: RegionId[];
  logOffset: number;
  multiplier: number;
  baseline: Metrics;
  globalPrior: Metrics;
}

interface ResidualSummary {
  mean: number;
  median: number;
  stationMeanRange: [number, number];
  stationMeans: Record<string, number>;
}

interface EmpiricalInterval {
  crossfitResidualCount: number;
  empiricalRadius: number;
  empiricalTestCoverage: number;
  heuristicCoverageTarget: number;
  meanIntervalWidth: number;
}

export interface SliceRow {
  id: string;
  label: string;
  eventCount: number;
  metrics: Record<MethodId, Metrics>;
  maeImprovement: number;
  maeImprovementPct: number;
}

export interface SliceDimension {
  id: string;
  label: string;
  rows: SliceRow[];
  improved: number;
  total: number;
}

export interface RegionView {
  id: RegionId;
  name: string;
  shortName: string;
  eyebrow: string;
  description: string;
  accent: string;
  baseline: Metrics;
  prediction: Metrics;
  methods: Record<MethodId, Metrics>;
  maeImprovement: number;
  maeImprovementPct: number;
  rmseImprovement: number;
  rmseImprovementPct: number;
  absoluteBiasChange: number;
  biasMagnitudeImproved: boolean;
  holdout: {
    trainEvents: number;
    testEvents: number;
    trainStations: number;
    testStations: number;
    trainStorms: number;
    testStorms: number;
    splitLabel: string;
  };
  intervals: {
    station: { clusters: number; repetitions: number; delta: Interval95; improvement: Interval95 };
    storm: { clusters: number; repetitions: number; delta: Interval95; improvement: Interval95 };
  };
  slices: SliceDimension[];
}

export interface FailureTaxonomyItem {
  name: string;
  status: string;
  category: "data" | "spatial" | "model" | "reproducibility";
  detail: string;
  evidence?: string;
}

export interface Citation {
  id: string;
  authors: string;
  year?: number;
  title: string;
  venue?: string;
  doi?: string;
  url?: string;
  implication: string;
}

export const REGION_ORDER: RegionId[] = ["rockies", "sierra", "swiss", "northeast"];
export const METHOD_ORDER: MethodId[] = ["baseline", "intercept", "ridgeThermal", "ridgeFull", "globalPrior"];

export const METHOD_META: Record<MethodId, { label: string; shortLabel: string; description: string; color: string }> = {
  baseline: {
    label: "Original MySnow Kuchera",
    shortLabel: "Kuchera",
    description: "The exact physical SLR baseline currently used for this comparison.",
    color: "#94a3b8",
  },
  intercept: {
    label: "Shrunk residual intercept",
    shortLabel: "Intercept",
    description: "A station-balanced regional log-residual correction with strong shrinkage.",
    color: "#a78bfa",
  },
  ridgeThermal: {
    label: "Thermal-profile ridge",
    shortLabel: "Thermal ridge",
    description: "Regularized residual model using surface and vertical thermal structure.",
    color: "#38bdf8",
  },
  ridgeFull: {
    label: "Profile-aware full ridge",
    shortLabel: "Full ridge",
    description: "Thermal structure plus precipitation, snowfall, elevation, baseline SLR, SWE proxy and profile completeness.",
    color: "#22d3ee",
  },
  globalPrior: {
    label: "Leave-one-region-out prior",
    shortLabel: "Global prior",
    description: "A station-balanced pooled offset learned from the other development regions; rejected as a global fallback.",
    color: "#f59e0b",
  },
};

const REGION_META: Record<RegionId, Pick<RegionView, "name" | "shortName" | "eyebrow" | "description" | "accent">> = {
  rockies: {
    name: "U.S. Rockies",
    shortName: "Rockies",
    eyebrow: "Continental mountain snow",
    description: "Three entirely unseen stations in a WY2025 holdout after WY2024 training.",
    accent: "#60a5fa",
  },
  sierra: {
    name: "Sierra Nevada",
    shortName: "Sierra",
    eyebrow: "Maritime-to-continental gradient",
    description: "Three unseen stations across western-slope, Tahoe high-country and eastern-Sierra terrain.",
    accent: "#22d3ee",
  },
  swiss: {
    name: "Swiss Alps",
    shortName: "Swiss Alps",
    eyebrow: "Dense multi-station Alpine test",
    description: "Twelve unseen SLF stations and the largest held-out event set in the diagnostic.",
    accent: "#a78bfa",
  },
  northeast: {
    name: "Northeast / New England",
    shortName: "Northeast",
    eyebrow: "Untouched transfer test",
    description: "Four unseen stations excluded from every model and hyperparameter selection decision.",
    accent: "#f472b6",
  },
};

const raw = diagnosticsJson as unknown as RawDiagnostics;

function titleize(value: string): string {
  return value
    .replace(/_/g, " ")
    .replace(/\b\w/g, (letter) => letter.toUpperCase())
    .replace(/ Hpa\b/g, " hPa")
    .replace(/ Wy\b/g, " WY");
}

function splitLabel(region: RegionId, temporal: Record<string, unknown>): string {
  if (region === "rockies") return "WY2024 → WY2025";
  if (region === "sierra") return "WY2023 → WY2024";
  if (region === "northeast") return "WY2024 → WY2025";
  const train = Array.isArray(temporal.train) ? temporal.train.join(" to ") : "2021-11-02 to 2021-12-30";
  const test = Array.isArray(temporal.test) ? temporal.test.join(" to ") : "2022-01-05 to 2022-04-30";
  return `${train} → ${test}`;
}

function makeSlices(regionResult: RawRegionResult): SliceDimension[] {
  const labels: Record<string, string> = {
    station: "Held-out station",
    storm: "Complete storm",
    elevationBand: "Elevation band",
    geographicSlice: "Geographic slice",
  };

  return Object.entries(regionResult.slices.byDimension).map(([dimensionId, slices]) => {
    const rows = Object.entries(slices).map(([id, slice]) => {
      const baselineMae = slice.metrics.baseline.mae;
      const predictionMae = slice.metrics.ridgeFull.mae;
      return {
        id,
        label: titleize(id),
        eventCount: slice.eventCount,
        metrics: slice.metrics,
        maeImprovement: baselineMae - predictionMae,
        maeImprovementPct: ((baselineMae - predictionMae) / baselineMae) * 100,
      };
    });
    const canonical = regionResult.slices.improvedSliceCounts[dimensionId]?.ridgeFull;
    return {
      id: dimensionId,
      label: labels[dimensionId] ?? titleize(dimensionId),
      rows,
      improved: canonical?.improved ?? rows.filter((row) => row.maeImprovement > 0).length,
      total: canonical?.total ?? rows.length,
    };
  });
}

function intervalView(interval: [number, number]): { delta: Interval95; improvement: Interval95 } {
  return {
    delta: interval,
    improvement: [-interval[1], -interval[0]],
  };
}

function buildRegion(id: RegionId): RegionView {
  const result = raw.fixedHoldoutEvaluation.results[id];
  const provenance = raw.provenanceAndSplitAssertions[id];
  const baseline = result.metrics.baseline;
  const prediction = result.metrics.ridgeFull;
  const stationInterval = result.clusterBootstrap95.station.intervals95["ridgeFull.maeDeltaVsBaseline"];
  const stormInterval = result.clusterBootstrap95.storm.intervals95["ridgeFull.maeDeltaVsBaseline"];
  const absoluteBiasChange = Math.abs(baseline.bias) - Math.abs(prediction.bias);

  return {
    id,
    ...REGION_META[id],
    baseline,
    prediction,
    methods: result.metrics,
    maeImprovement: baseline.mae - prediction.mae,
    maeImprovementPct: ((baseline.mae - prediction.mae) / baseline.mae) * 100,
    rmseImprovement: baseline.rmse - prediction.rmse,
    rmseImprovementPct: ((baseline.rmse - prediction.rmse) / baseline.rmse) * 100,
    absoluteBiasChange,
    biasMagnitudeImproved: absoluteBiasChange >= 0,
    holdout: {
      trainEvents: provenance.trainEventCount,
      testEvents: provenance.testEventCount,
      trainStations: provenance.trainStations.length,
      testStations: provenance.testStations.length,
      trainStorms: provenance.trainStormCount,
      testStorms: provenance.testStormCount,
      splitLabel: splitLabel(id, provenance.temporalConstraint),
    },
    intervals: {
      station: {
        clusters: result.clusterBootstrap95.station.clusterCount,
        repetitions: result.clusterBootstrap95.station.repetitions,
        ...intervalView(stationInterval),
      },
      storm: {
        clusters: result.clusterBootstrap95.storm.clusterCount,
        repetitions: result.clusterBootstrap95.storm.repetitions,
        ...intervalView(stormInterval),
      },
    },
    slices: makeSlices(result),
  };
}

export const FAILURE_TAXONOMY: FailureTaxonomyItem[] = [
  {
    name: "Pacific Northwest",
    status: "No cleared admissible labels",
    category: "data",
    detail: "Audited operational networks did not establish independent fresh-depth plus fresh-SWE measurements over one interval. CoCoRaHS was the only plausible direct source, but reuse and commercial-training authorization was not positively established.",
    evidence: "data/pnw-slr-research/pnw-blocker-report.json",
  },
  {
    name: "Canadian Rockies",
    status: "No multi-station independent labels",
    category: "spatial",
    detail: "Four ordinary ECCC stations were entirely exact 10:1 and a fifth was partly exact 10:1. One potentially non-circular station cannot support unseen-station validation.",
    evidence: "data/eccc/canadian-rockies/reports/decision.md",
  },
  {
    name: "Japan",
    status: "Inadequate spatial support",
    category: "spatial",
    detail: "NIED Shinjo has genuine paired daily fresh-snow depth and SWE, but only at one fixed station. JMA positive snow-depth change remains an invalid fresh-snow label.",
  },
  {
    name: "Northeast / New England",
    status: "Genuine transfer failure—now diagnosed",
    category: "model",
    detail: "The old intercept hierarchy moved MAE from 4.913 to 4.946 on 80 unseen-station events because training and test residuals changed sign and held-out stations were heterogeneous.",
  },
  {
    name: "Northern Appalachians",
    status: "Physical-label gate failure",
    category: "data",
    detail: "The alternate mask did not produce independent, same-window fresh-depth and fresh-SWE labels, so modeling never began.",
    evidence: "data/northern-appalachians-slr-research/northern-appalachians-blocker-report.json",
  },
  {
    name: "Great Lakes / Upper Midwest",
    status: "No cleared admissible labels",
    category: "data",
    detail: "Every candidate paired WESF row had source flag N / CoCoRaHS. PRCP and total-pack WESD are not substitutes for independently measured fresh-snow SWE.",
    evidence: "data/great-lakes-slr-research/great-lakes-blocker-report.json",
  },
  {
    name: "Alaska",
    status: "No cleared admissible labels",
    category: "data",
    detail: "All 2,770 threshold-and-field-pass events retained source N on WESF; zero events had proven separate-core provenance.",
    evidence: "data/alaska-slr-research/alaska-blocker-report.json",
  },
  {
    name: "Intermountain West",
    status: "Reproducibility failure",
    category: "reproducibility",
    detail: "The exploratory audit could not be reproduced reliably enough to satisfy the acceptance gate. No artifact was retained.",
  },
  {
    name: "Broad remaining-U.S. audit",
    status: "Zero strict admissible events",
    category: "data",
    detail: "The 239-station audit left 5,205 source-N near misses and 323 exact-10:1 rejections. Broader geometry cannot repair missing provenance.",
  },
];

export const CITATIONS: Citation[] = [
  {
    id: "baxter-2005",
    authors: "Baxter, Graves & Moore",
    year: 2005,
    title: "A Climatology of Snow-to-Liquid Ratio for the Contiguous United States",
    venue: "Weather and Forecasting 20, 729–744",
    doi: "10.1175/WAF856.1",
    url: "https://journals.ametsoc.org/view/journals/wefo/20/5/waf856_1.xml",
    implication: "SLR varies geographically and through in-cloud, subcloud and surface processes; strict provenance is needed because exact-10:1 reports, settling, mixed precipitation and gauge undercatch contaminate labels.",
  },
  {
    id: "roebber-2003",
    authors: "Roebber, Bruening, Schultz & Cortinas",
    year: 2003,
    title: "Improving Snowfall Forecasting by Diagnosing Snow Density",
    venue: "Weather and Forecasting 18, 264–287",
    doi: "10.1175/1520-0434(2003)018<0264:ISFBDS>2.0.CO;2",
    implication: "Vertical temperature and humidity, surface wind and liquid amount motivate a profile-aware residual model. Missing humidity and wind are limitations, not values to synthesize.",
  },
  {
    id: "grossi-2017",
    authors: "Grossi et al.",
    year: 2017,
    title: "Snow Precipitation Measured by Gauges: Systematic Error Estimation and Data Series Correction in the Central Italian Alps",
    venue: "Water 9, 461",
    doi: "10.3390/w9070461",
    url: "https://www.mdpi.com/2073-4441/9/7/461",
    implication: "Gauge undercatch and roughly 7% variation in paired manual measurements show why cluster-aware uncertainty can matter more than small point-estimate gains.",
  },
  {
    id: "veals-2025",
    authors: "Veals et al.",
    year: 2025,
    title: "Predicting Snow-to-Liquid Ratio in the Mountains of the Western United States",
    venue: "Weather and Forecasting",
    doi: "10.1175/WAF-D-24-0233.1",
    url: "https://www.inscc.utah.edu/~u0813947/sharing/Veals_etal_2025_v1.5.pdf",
    implication: "Multi-site mountain models improve with atmospheric covariates, while pack-depth differences and wind-driven gauge undercatch remain unsuitable foundations for clean labels.",
  },
  {
    id: "noaa-ml",
    authors: "NOAA Institutional Repository",
    title: "Snow-to-Liquid Ratio Prediction over the Contiguous United States Using Machine Learning",
    venue: "Weather and Forecasting 41, 317–338",
    url: "http://repository.library.noaa.gov/view/noaa/73136",
    implication: "Profile-aware machine learning can beat legacy methods, but its CoCoRaHS labels do not resolve MySnow’s independent authorization requirement or extreme-SLR uncertainty.",
  },
];

export const slrResearchViewModel = {
  metadata: {
    schemaVersion: raw.schemaVersion,
    status: raw.status,
    purpose: raw.purpose,
    productionEnablement: raw.productionEnablement,
    artifactProduced: raw.artifactProduced,
    diagnosticsCommit: "b2f1dc0dc2f30cbaf05070b439b942a6b8e43f7b",
    researchCommit: "8650cda7b6d3ab10309dc3f4dcda46917990b86c",
    diagnosticsSha256: "0d8ae4312cd117540f326d68ba80c865f61d8c94479bdb241da6f842ecbebee9",
    regenerationCommand: "PYTHONPATH=training/slr_calibration/src python training/slr_calibration/scripts/slr_methodology_diagnostics.py",
    fixedHoldoutPolicy: raw.fixedHoldoutEvaluation.policy,
    bootstrapSeedPolicy: raw.determinism.bootstrapSeedPolicy,
    stdlibOnly: raw.determinism.stdlibOnly,
  },
  regions: REGION_ORDER.map(buildRegion),
  selectedModels: raw.trainingGroupedCvSelection.selected,
  selection: {
    objective: raw.trainingGroupedCvSelection.objective,
    holdoutIsolation: raw.trainingGroupedCvSelection.holdoutIsolation,
    foldFamilies: raw.trainingGroupedCvSelection.foldFamilies,
    balances: raw.balancingDefinitions,
  },
  globalPrior: raw.globalPriorLeaveOneRegionOut,
  northeast: raw.northeastUntouchedEvaluation,
  predictors: raw.predictors,
  usLabelWarning: raw.usLabelProvenanceWarning,
  limitations: raw.limitations,
  failureTaxonomy: FAILURE_TAXONOMY,
  citations: CITATIONS,
};

export type SlrResearchViewModel = typeof slrResearchViewModel;

export function getRegionView(id: RegionId): RegionView {
  const region = slrResearchViewModel.regions.find((candidate) => candidate.id === id);
  if (!region) throw new Error(`Unknown SLR research region: ${id}`);
  return region;
}
