(() => {
 const rows=document.querySelector('#quick-rows'), add=document.querySelector('#add-quick');
 const renumber=()=>{rows.querySelectorAll('.row-number').forEach((n,i)=>n.textContent=i+1);add.disabled=rows.children.length>=100;};
 add.addEventListener('click',()=>{
  const last=rows.lastElementChild, row=last.cloneNode(true);
  row.querySelectorAll('input').forEach(input=>{if(!['category','unit'].includes(input.name))input.value=input.type==='number'?'0':'';});
  rows.append(row);renumber();row.querySelector('[name=name]').focus();
 });
 rows.addEventListener('click',e=>{if(e.target.matches('.remove-quick')&&rows.children.length>1){e.target.closest('.quick-row').remove();renumber();}});
 document.querySelector('#quick-add-form').addEventListener('submit',e=>{if(!e.target.checkValidity())return; e.target.querySelectorAll('button').forEach(b=>b.disabled=true);});
})();
