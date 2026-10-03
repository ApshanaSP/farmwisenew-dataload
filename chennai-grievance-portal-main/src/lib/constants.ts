// Canonical list of the 16 GCC departments. This MUST stay in sync with
// the `departments` table seeded by schema.sql (same order, same names).
// Used for: the AI classifier's allowed-values list and the fallback
// keyword classifier. The UI itself always reads departments from the
// database via /api/departments.
export const DEPARTMENT_NAMES = [
  "Revenue Department",
  "Engineering Department (Town Planning & Building Permissions)",
  "Electrical Department",
  "Solid Waste Management Department",
  "Storm Water Drain Department",
  "Bridges Department",
  "Health Department",
  "Family Welfare Department",
  "Education Department",
  "Parks & Play Fields Department",
  "Buildings Department",
  "Mechanical Engineering Department",
  "Land & Estate Department",
  "General Administration",
  "Financial Management Unit",
  "Council Department"
] as const;

export const DEFAULT_FALLBACK_DEPARTMENT = "Revenue Department";

export const PASSWORD_RULES = {
  minLength: 8,
  requireUppercase: true,
  requireLowercase: true,
  requireNumber: true,
  requireSpecial: true,
  noSpaces: true
};

export const OTP_LENGTH = 6;
export const OTP_EXPIRY_MINUTES = 10;
export const OTP_MAX_ATTEMPTS = 5;
export const OTP_RESEND_COOLDOWN_SECONDS = 60;

export const COMPLAINT_STATUSES = [
  "Complaint Filed",
  "Pending Approval",
  "Approved by Department Officer",
  "In Progress",
  "Completed - Pending Collector Verification",
  "Verified by Collector",
  "Rejected"
] as const;

export const CHENNAI_CENTER = { lat: 13.0827, lng: 80.2707 };

/**
 * Primary destinations for a signed-in citizen. These live in the header
 * because the citizen landing page no longer repeats them as body actions.
 */
export const CITIZEN_NAV = [
  { href: "/citizen/file-complaint", label: "File a Complaint" },
  { href: "/citizen/track-complaints", label: "Track Status" }
];
