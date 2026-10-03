import { DEPARTMENT_NAMES, DEFAULT_FALLBACK_DEPARTMENT } from "@/lib/constants";

export interface ClassificationResult {
  department: string;
  needsManualReview: boolean;
  method: "openai" | "keyword" | "fallback";
}

// Keyword → department fallback classifier. Used if the OpenAI API is not
// configured, fails, or returns something outside the allowed list.
const KEYWORD_MAP: { keywords: string[]; department: string }[] = [
  { keywords: ["street light", "streetlight", "lamp post", "electric pole", "cable", "crematorium", "electricity"], department: "Electrical Department" },
  { keywords: ["garbage", "waste", "trash", "conservancy", "dustbin", "bin overflow"], department: "Solid Waste Management Department" },
  { keywords: ["sewage", "drain", "stagnant water", "stagnation", "storm water", "flooding", "waterlogging"], department: "Storm Water Drain Department" },
  { keywords: ["pothole", "road damage", "debris", "illegal construction", "building permit", "unauthorized building", "road repair", "private street"], department: "Engineering Department (Town Planning & Building Permissions)" },
  { keywords: ["property tax", "encroachment", "footpath", "parking fee", "profession tax", "advertisement tax", "ownership"], department: "Revenue Department" },
  { keywords: ["stray dog", "stray animal", "food adulteration", "birth certificate", "death certificate", "mosquito", "sanitation", "dispensary"], department: "Health Department" },
  { keywords: ["maternity", "child welfare", "immunization", "vaccination"], department: "Family Welfare Department" },
  { keywords: ["school", "nutritious meal", "mid-day meal", "education"], department: "Education Department" },
  { keywords: ["park", "playground", "swimming pool", "tree", "fallen tree"], department: "Parks & Play Fields Department" },
  { keywords: ["community hall", "public convenience", "public toilet", "hospital construction", "school building"], department: "Buildings Department" },
  { keywords: ["corporation vehicle", "lorry", "truck maintenance"], department: "Mechanical Engineering Department" },
  { keywords: ["lease", "corporation land", "corporation shop", "estate"], department: "Land & Estate Department" },
  { keywords: ["staff", "administration", "admin matter"], department: "General Administration" },
  { keywords: ["budget", "loan", "grant", "finance"], department: "Financial Management Unit" },
  { keywords: ["mayor", "council", "secretariat"], department: "Council Department" },
  { keywords: ["bridge", "subway", "causeway"], department: "Bridges Department" }
];

function keywordClassify(text: string): string | null {
  const lower = text.toLowerCase();
  for (const entry of KEYWORD_MAP) {
    if (entry.keywords.some((kw) => lower.includes(kw))) {
      return entry.department;
    }
  }
  return null;
}

async function openAiClassify(text: string): Promise<string | null> {
  const apiKey = process.env.OPENAI_API_KEY;
  if (!apiKey) return null;

  try {
    const systemPrompt = `You are a strict classifier for the Greater Chennai Corporation's citizen grievance portal.
Classify the citizen's complaint description into EXACTLY ONE of these 16 departments (return the name exactly as written, no changes):
${DEPARTMENT_NAMES.map((d) => `- ${d}`).join("\n")}

Respond with ONLY a JSON object in this exact shape, nothing else:
{"department": "<one of the 16 department names above, verbatim>"}`;

    const response = await fetch("https://api.openai.com/v1/chat/completions", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${apiKey}`
      },
      body: JSON.stringify({
        model: "gpt-4o-mini",
        temperature: 0,
        response_format: { type: "json_object" },
        messages: [
          { role: "system", content: systemPrompt },
          { role: "user", content: text }
        ]
      })
    });

    if (!response.ok) {
      console.error("[ai-classifier] OpenAI API error:", response.status, await response.text());
      return null;
    }

    const data = await response.json();
    const content = data?.choices?.[0]?.message?.content;
    if (!content) return null;

    const parsed = JSON.parse(content);
    const department = parsed?.department;

    if (typeof department === "string" && (DEPARTMENT_NAMES as readonly string[]).includes(department)) {
      return department;
    }
    return null;
  } catch (err) {
    console.error("[ai-classifier] OpenAI classification failed:", err);
    return null;
  }
}

/**
 * Classifies a free-text "Others" complaint description into one of the
 * 16 departments. Tries OpenAI first (if configured), falls back to a
 * keyword-based classifier, and finally defaults to Revenue Department
 * with needsManualReview = true.
 */
export async function classifyComplaint(description: string): Promise<ClassificationResult> {
  const aiResult = await openAiClassify(description);
  if (aiResult) {
    return { department: aiResult, needsManualReview: false, method: "openai" };
  }

  const keywordResult = keywordClassify(description);
  if (keywordResult) {
    return { department: keywordResult, needsManualReview: false, method: "keyword" };
  }

  return { department: DEFAULT_FALLBACK_DEPARTMENT, needsManualReview: true, method: "fallback" };
}
