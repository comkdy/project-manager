import {forwardRef,type HTMLAttributes} from 'react';
import {snapGridStyle,type GridMetrics} from './dashboardGrid';

// Forward the DOM ref and event handlers supplied by react-grid-layout.
export const GridCard=forwardRef<HTMLDivElement,HTMLAttributes<HTMLDivElement>&{grid:GridMetrics}>(function GridCard({grid,style={},className='',...props},ref){
 const interacting=className.includes('resizing')||className.includes('react-draggable-dragging');
 return <div {...props} ref={ref} className={className} style={interacting?snapGridStyle(style,grid):style}/>;
});
