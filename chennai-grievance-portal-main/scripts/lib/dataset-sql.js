/**
 * The one definition of the complaint dataset's columns.
 *
 * Shared by every consumer so the column list can never drift:
 *   - scripts/export-complaints.js        (npm run export:data / export:excel)
 *   - scripts/lib/dataset-export.js       (npm run dataset:export, the generator)
 *   - src/lib/dataset/append.ts           (live append after a citizen files)
 *   - scripts/validate-grievance-dataset.js
 *
 * The SELECT list and joins are exactly the ones export-complaints.js has
 * always used; only the WHERE / ORDER BY and optional trailing columns vary
 * per caller.
 */

/** Columns 1-31 of grievances.csv, and the Complaints sheet of the workbook. */
const COMPLAINT_COLUMNS = [
  "Complaint No", "Filed On", "Status", "Rejected At", "Complaint Type",
  "Complaint Sub Type", "Department", "Routing", "Routing Basis",
  "Zone No", "Zone", "Ward", "Ward Determined By", "Area", "Locality",
  "Street", "Street Source", "Landmark", "Location PIN", "Latitude",
  "Longitude", "Title", "Details", "Anonymous", "Complainant", "Gender",
  "Mobile", "Email", "Complainant Address", "Photo Attached", "Last Updated"
];

/** Columns 1-5 of grievance_status_history.csv, and the Status History sheet. */
const HISTORY_COLUMNS = ["Complaint No", "Status", "Changed By Role", "Remarks", "Changed On"];

// LEFT JOINs throughout: a complaint uses either the GCC reference data or
// the original locality/street tables, and a manually typed street has
// neither, so an INNER JOIN would silently drop rows.
const COMPLAINT_SELECT_LIST = `
        c.complaint_code                            AS 'Complaint No',
        DATE_FORMAT(c.created_at, '%Y-%m-%d %H:%i') AS 'Filed On',
        c.status                                    AS 'Status',
        c.rejected_stage                            AS 'Rejected At',
        cc.name                                     AS 'Complaint Type',
        COALESCE(cs.label, ct.name)                 AS 'Complaint Sub Type',
        d.name                                      AS 'Department',
        CASE
          WHEN c.needs_manual_review = 1 THEN 'Needs officer review'
          ELSE 'Auto-routed'
        END                                         AS 'Routing',
        cs.mapping_status                           AS 'Routing Basis',
        z.zone_number                               AS 'Zone No',
        z.zone_name                                 AS 'Zone',
        c.ward_number                               AS 'Ward',
        c.ward_source                               AS 'Ward Determined By',
        ga.name                                     AS 'Area',
        COALESCE(gl.name, l.name)                   AS 'Locality',
        COALESCE(
          gs.name,
          NULLIF(TRIM(CONCAT(COALESCE(c.manual_street_name,''),' ',COALESCE(c.street_type,''))), ''),
          s.name
        )                                           AS 'Street',
        CASE WHEN c.gcc_street_id IS NULL AND c.manual_street_name IS NOT NULL
             THEN 'Typed by citizen' ELSE 'From GCC list' END AS 'Street Source',
        c.specific_location                         AS 'Landmark',
        c.location_pincode                          AS 'Location PIN',
        c.latitude                                  AS 'Latitude',
        c.longitude                                 AS 'Longitude',
        c.title                                     AS 'Title',
        c.description                               AS 'Details',
        CASE WHEN c.is_anonymous = 1 THEN 'Yes' ELSE 'No' END AS 'Anonymous',
        CASE WHEN c.is_anonymous = 1 THEN NULL
             ELSE TRIM(CONCAT(COALESCE(c.initials,''),' ',c.first_name,' ',COALESCE(c.last_name,'')))
        END                                         AS 'Complainant',
        CASE WHEN c.is_anonymous = 1 THEN NULL ELSE c.gender END      AS 'Gender',
        CASE WHEN c.is_anonymous = 1 THEN NULL ELSE c.mobile_number END AS 'Mobile',
        CASE WHEN c.is_anonymous = 1 THEN NULL ELSE c.email END        AS 'Email',
        CASE WHEN c.is_anonymous = 1 THEN NULL ELSE c.street_address END AS 'Complainant Address',
        CASE WHEN c.media_path IS NULL THEN 'No' ELSE 'Yes' END       AS 'Photo Attached',
        DATE_FORMAT(c.updated_at, '%Y-%m-%d %H:%i')                   AS 'Last Updated'`;

const COMPLAINT_FROM = `
     FROM complaints c
     LEFT JOIN departments d           ON d.id  = c.department_id
     LEFT JOIN complaint_subtypes cs   ON cs.id = c.complaint_subtype_id
     LEFT JOIN complaint_categories cc ON cc.id = cs.category_id
     LEFT JOIN complaint_types ct      ON ct.id = c.complaint_type_id
     LEFT JOIN zones z                 ON z.id  = c.zone_id
     LEFT JOIN gcc_areas ga            ON ga.id = c.gcc_area_id
     LEFT JOIN gcc_localities gl       ON gl.id = c.gcc_locality_id
     LEFT JOIN gcc_streets gs          ON gs.id = c.gcc_street_id
     LEFT JOIN localities l            ON l.id  = c.locality_id
     LEFT JOIN streets s               ON s.id  = c.street_id`;

/**
 * Builds the complaints SELECT.
 *   where   - SQL after WHERE (without the keyword), or "" for everything
 *   orderBy - SQL after ORDER BY
 *   extra   - additional select-list entries appended after column 31
 */
function complaintsSql({ where = "", orderBy = "c.created_at DESC", extra = [] } = {}) {
  const list = COMPLAINT_SELECT_LIST + (extra.length ? ",\n        " + extra.join(",\n        ") : "");
  return `SELECT${list}${COMPLAINT_FROM}
     ${where ? "WHERE " + where : ""}
     ORDER BY ${orderBy}`;
}

/** Builds the status-history SELECT. Same parameters as complaintsSql. */
function historySql({ where = "", orderBy = "c.complaint_code, h.created_at", extra = [] } = {}) {
  const list = `
        c.complaint_code                             AS 'Complaint No',
        h.status                                     AS 'Status',
        h.stage                                      AS 'Changed By Role',
        h.remarks                                    AS 'Remarks',
        DATE_FORMAT(h.created_at, '%Y-%m-%d %H:%i')  AS 'Changed On'` +
    (extra.length ? ",\n        " + extra.join(",\n        ") : "");
  return `SELECT${list}
      FROM complaint_status_history h
      JOIN complaints c ON c.id = h.complaint_id
      ${where ? "WHERE " + where : ""}
      ORDER BY ${orderBy}`;
}

module.exports = { COMPLAINT_COLUMNS, HISTORY_COLUMNS, complaintsSql, historySql };
