/** Deep-link targets for Investor Academy modules. */
export const ACADEMY_MODULES = {
  momentumPricing: 'L2M6',
};

export function academyModulePath(moduleId, query = {}) {
  const params = new URLSearchParams({ module: moduleId });
  Object.entries(query).forEach(([key, value]) => {
    if (value != null && String(value).trim() !== '') {
      params.set(key, String(value).trim());
    }
  });
  return `/learning?${params.toString()}`;
}

/** @deprecated use momentumAcademyPath from utils/momentumAcademyContext */
export const MOMENTUM_ACADEMY_PATH = academyModulePath(ACADEMY_MODULES.momentumPricing);
