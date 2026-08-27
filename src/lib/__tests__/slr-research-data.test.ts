import { describe, expect, it } from "vitest";
import { getRegionView, slrResearchViewModel } from "../slr-research-data";

describe("SLR research view model", () => {
  it("preserves the canonical regional headline metrics", () => {
    expect(getRegionView("rockies").baseline.mae).toBe(5.180194);
    expect(getRegionView("rockies").prediction.mae).toBe(2.928326);
    expect(getRegionView("sierra").prediction.rmse).toBe(3.331149);
    expect(getRegionView("swiss").prediction.mae).toBe(3.290768);
    expect(getRegionView("northeast").prediction.mae).toBe(4.146764);
  });

  it("derives improvement percentages and signed bias honestly", () => {
    const sierra = getRegionView("sierra");
    expect(sierra.maeImprovement).toBeCloseTo(1.073483, 6);
    expect(sierra.maeImprovementPct).toBeCloseTo(28.4971, 3);
    expect(sierra.absoluteBiasChange).toBeCloseTo(-0.277123, 6);
    expect(sierra.biasMagnitudeImproved).toBe(false);

    const northeast = getRegionView("northeast");
    expect(northeast.baseline.bias).toBe(-2.042484);
    expect(northeast.prediction.bias).toBe(-2.130908);
  });

  it("reverses stored delta intervals only for improvement display", () => {
    const rockies = getRegionView("rockies");
    expect(rockies.intervals.station.delta).toEqual([-2.917221, -1.859715]);
    expect(rockies.intervals.station.improvement).toEqual([1.859715, 2.917221]);
    expect(rockies.intervals.storm.delta[1]).toBeLessThan(0);
  });

  it("uses the canonical slice counts and fixed holdout support", () => {
    const northeast = getRegionView("northeast");
    expect(northeast.holdout).toMatchObject({ testEvents: 80, testStations: 4, testStorms: 27 });
    expect(northeast.slices.find((slice) => slice.id === "station")).toMatchObject({ improved: 4, total: 4 });
    expect(northeast.slices.find((slice) => slice.id === "storm")).toMatchObject({ improved: 21, total: 27 });
    expect(northeast.slices.find((slice) => slice.id === "geographicSlice")).toMatchObject({ improved: 4, total: 4 });
  });

  it("keeps the global fallback rejected and runtime metadata off", () => {
    expect(slrResearchViewModel.globalPrior.decision).toBe("no-global-fallback");
    expect(slrResearchViewModel.globalPrior.northeastFinal.baseline.mae).toBe(4.913366);
    expect(slrResearchViewModel.globalPrior.northeastFinal.globalPrior.mae).toBe(5.081097);
    expect(slrResearchViewModel.metadata.productionEnablement).toBe(false);
    expect(slrResearchViewModel.metadata.artifactProduced).toBe(false);
  });

  it("never claims unsupported raw event-level data", () => {
    expect(slrResearchViewModel.predictors.allAvailableFeatureNames).toHaveLength(32);
    expect(slrResearchViewModel.failureTaxonomy).toHaveLength(9);
    expect(slrResearchViewModel.citations).toHaveLength(5);
  });
});
