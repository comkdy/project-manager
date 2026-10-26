let plotlyPromise:Promise<any>|undefined;
export const getPlotly=()=>plotlyPromise??(plotlyPromise=import('plotly.js-cartesian-dist-min').then(m=>m.default));
let fullPlotlyPromise:Promise<any>|undefined;
export const getFullPlotly=()=>fullPlotlyPromise??(fullPlotlyPromise=import('plotly.js-dist-min').then(m=>m.default));
