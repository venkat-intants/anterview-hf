/**
 * Analytics defaults, so a test only states the numbers it cares about.
 *
 * The roll-up blocks (openings, velocity, conversion) exist on every response
 * and most tests have no opinion about them — spelling them out in each
 * fixture is how one gets forgotten when the type next grows.
 */
export function analyticsDefaults() {
  return {
    openings: { open: 0, paused: 0, closed: 0 },
    velocity: {
      median_time_to_hire_days: null,
      hires_measured: 0,
      applications_last_7d: 0,
      applications_prev_7d: 0,
    },
    conversion: {
      applied: 0,
      ever_shortlisted: 0,
      ever_sat_exam: 0,
      ever_interviewed: 0,
      ever_hired: 0,
      pct_shortlisted: null,
      pct_sat_exam: null,
      pct_interviewed: null,
      pct_hired: null,
    },
  };
}
