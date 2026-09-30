(() => {
 const rows=[...document.querySelectorAll('.preview-row')], prev=document.querySelector('#preview-prev'),next=document.querySelector('#preview-next');
 if(!next)return;let page=0;
 function show(){rows.forEach((r,i)=>r.hidden=i<page*50||i>=(page+1)*50);prev.disabled=page===0;next.disabled=(page+1)*50>=rows.length;document.querySelector('#preview-page').textContent=`Page ${page+1} of ${Math.ceil(rows.length/50)}`;}
 prev.addEventListener('click',()=>{page--;show();});next.addEventListener('click',()=>{page++;show();});
})();
