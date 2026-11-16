import test from 'node:test';
import assert from 'node:assert/strict';
import {canAllocatePrinter,allocationNeedsReview} from '../studio/src/printer-allocation.js';

const option={printer_id:'a1',fit:'fits',selection_available:true};
const data={ready:true,revision:'v002',selected_printer_id:'a1',options:[option]};

test('only the checked current model can be allocated to a size-compatible printer',()=>{
  assert.equal(canAllocatePrinter(data,option,'v002'),true);
  assert.equal(canAllocatePrinter(data,{...option,online:false},'v002'),true);
  for(const candidate of [{...option,fit:'too_large'},{...option,fit:'unknown'},{...option,selection_available:false},null])assert.equal(canAllocatePrinter(data,candidate,'v002'),false);
  assert.equal(canAllocatePrinter(data,option,'v003'),false);
  assert.equal(canAllocatePrinter({...data,ready:false},option,'v002'),false);
  assert.equal(canAllocatePrinter(null,option,null),false);
});

test('a changed model or an unavailable selected device needs review while an unallocated project stays unallocated',()=>{
  assert.equal(allocationNeedsReview(data,'v002'),false);
  assert.equal(allocationNeedsReview(data,'v003'),true);
  assert.equal(allocationNeedsReview({...data,assignment_current:false},'v002'),true);
  assert.equal(allocationNeedsReview({...data,options:[{...option,fit:'too_large'}]},'v002'),true);
  assert.equal(allocationNeedsReview({...data,options:[]},'v002'),true);
  assert.equal(allocationNeedsReview({...data,selected_printer_id:null},'v002'),false);
  assert.equal(allocationNeedsReview(null,null),false);
});
