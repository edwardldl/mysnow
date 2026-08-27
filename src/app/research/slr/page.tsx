import type { Metadata } from "next";
import SlrResearchDashboard from "@/components/slr-research/SlrResearchDashboard";
import { slrResearchViewModel } from "@/lib/slr-research-data";

export const metadata: Metadata = {
  title: "Regional SLR Model Evaluation | MySnow Research",
  description: "Research-only comparison of MySnow's profile-aware regional SLR diagnostic with the original Kuchera algorithm across held-out regions.",
};

export const dynamic = "force-static";

export default function RegionalSlrResearchPage() {
  return <SlrResearchDashboard data={slrResearchViewModel} />;
}
