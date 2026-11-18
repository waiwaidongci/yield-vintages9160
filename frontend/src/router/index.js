export const pages=[['overview','业务总览'],['sources','业务底稿'],['inspections','抽检导入'],['policies','口径版本'],['reconciliations','抽检对账'],['runs','核算与复核'],['deliveries','定时交付'],['audit','过程留痕']];
export const currentPage=()=>location.hash.slice(1)||'overview';
