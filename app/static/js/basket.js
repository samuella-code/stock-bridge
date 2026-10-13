(() => {
 document.querySelectorAll('.basket').forEach(basket=>{
  const mode=basket.dataset.mode,search=basket.querySelector('.basket-search'),results=basket.querySelector('.basket-results'),lines=basket.querySelector('.basket-lines'),message=basket.querySelector('.basket-message');
  const form=basket.closest('form'),submitButton=form.querySelector('button[type=submit]'),serverDisabled=submitButton.disabled;let timer,controller,sequence=0,saleInvalid=false;
  const money=value=>'₦'+Number(value).toLocaleString('en-NG',{minimumFractionDigits:2,maximumFractionDigits:2});
  function total(){let amount=0;saleInvalid=false;
   lines.querySelectorAll('.basket-line').forEach(row=>{
    const input=row.querySelector('[name=quantity]'),q=Number(input.value||0),priceInput=row.querySelector('.line-price'),price=Number(priceInput.value||0);
    const subtotal=q*price;amount+=mode==='sale'&&!Number.isFinite(subtotal)?0:subtotal;row.querySelector('.line-total').textContent=money(mode==='sale'&&!Number.isFinite(subtotal)?0:subtotal);
    if(mode==='sale'){
     const warning=row.querySelector('.stock-warning'),stock=Number(row.dataset.stock);
     const excess=q>stock;warning.textContent=excess?`Only ${stock} available. Reduce the quantity.`:(!input.validity.valid?'Enter a valid whole quantity of at least 1.':(!priceInput.validity.valid?'Enter a valid non-negative unit price with at most two decimal places.':''));
     warning.hidden=!warning.textContent;input.setAttribute('aria-invalid',String(excess||!input.validity.valid));
     priceInput.setAttribute('aria-invalid',String(!priceInput.validity.valid));
     saleInvalid=saleInvalid||excess||!input.validity.valid||!priceInput.validity.valid;
    }
   });
   basket.querySelector('.basket-total').textContent='Total: '+money(amount);
   if(mode==='sale'){
    const count=basket.querySelector('.basket-count'),empty=basket.querySelector('.basket-empty');
    if(count)count.textContent=`${lines.children.length} products selected`;
    if(empty)empty.hidden=lines.children.length>0;
    submitButton.disabled=serverDisabled||saleInvalid;
   }
  }
  function add(product){const existing=[...lines.children].find(r=>r.dataset.id===String(product.id));if(existing){existing.querySelector('[name=quantity]').focus();message.textContent='This product is already in the basket. Change its quantity there.';return;}
   if(lines.children.length>=100){message.textContent='A transaction can contain up to 100 products.';return;}
   const row=document.createElement('fieldset');row.className='basket-line';row.dataset.id=product.id;if(mode==='sale')row.dataset.stock=product.stock;
   const title=document.createElement('legend');title.textContent=product.name;row.append(title);
   const stock=document.createElement('small');stock.textContent=`${product.stock} ${product.unit} currently available`;row.append(stock);if(mode==='sale'){stock.id=`sale-stock-${product.id}`;}
   const pid=document.createElement('input');pid.type='hidden';pid.name='product_id';pid.value=product.id;row.append(pid);
   function field(labelText,name,value,step){const label=document.createElement('label');label.textContent=labelText;const input=document.createElement('input');input.type='number';input.name=name;input.value=value;input.min=name==='quantity'?'1':'0';input.step=step;input.inputMode=step==='1'?'numeric':'decimal';input.required=true;label.append(input);row.append(label);return input;}
   const qty=field(mode==='sale'?'Quantity':'Quantity received','quantity','1','1');if(mode==='sale'){qty.max=product.stock;qty.setAttribute('aria-describedby',`sale-stock-${product.id} sale-warning-${product.id}`);}
   const price=field(mode==='sale'?'Unit price (₦)':'Purchase cost per unit (₦)',mode==='sale'?'unit_price':'unit_cost',mode==='sale'?product.price:product.cost,'0.01');price.className='line-price';if(mode==='sale')price.setAttribute('aria-describedby',`sale-warning-${product.id}`);
   if(mode==='sale'){const warning=document.createElement('p');warning.className='stock-warning';warning.id=`sale-warning-${product.id}`;warning.setAttribute('role','status');warning.hidden=true;row.append(warning);}
   const subtotal=document.createElement('strong');subtotal.className='line-total';row.append(subtotal);const remove=document.createElement('button');remove.type='button';remove.className='secondary-btn';remove.textContent='Remove item';remove.onclick=()=>{row.remove();total();if(mode==='sale')search.focus();};row.append(remove);lines.append(row);results.replaceChildren();search.value='';message.textContent='Product added. Add another or review your basket.';if(mode==='sale')search.focus();else qty.focus();total();
  }
  async function lookup(term){controller?.abort();controller=new AbortController();const mine=++sequence;results.textContent='Searching…';
   try{const response=await fetch(`${basket.dataset.lookup}?q=${encodeURIComponent(term)}`,{signal:controller.signal,headers:{Accept:'application/json'}});if(!response.ok)throw Error();const data=await response.json();if(mine!==sequence)return;results.replaceChildren();
    if(!data.products.length){results.textContent='No matching products. Try another name, SKU or category.';return;}
    data.products.forEach(product=>{const button=document.createElement('button');button.type='button';button.className='search-result';button.textContent=`${product.name} · ${product.stock} ${product.unit} · ${money(mode==='sale'?product.price:product.cost)}`;button.disabled=mode==='sale'&&product.stock===0;button.onclick=()=>add(product);results.append(button);});
   }catch(error){if(error.name!=='AbortError'&&mine===sequence)results.textContent='Could not search products. Check your connection and try again.';}
  }
  search.addEventListener('input',()=>{clearTimeout(timer);controller?.abort();sequence++;const term=search.value.trim();if(!term){results.replaceChildren();return;}timer=setTimeout(()=>lookup(term),200);});
  search.addEventListener('keydown',e=>{if(e.key==='Enter'){e.preventDefault();clearTimeout(timer);if(search.value.trim())lookup(search.value.trim());}});
  if(mode==='sale'){const picker=basket.querySelector('.sale-product-picker');if(picker)picker.addEventListener('click',()=>{search.value='';lookup('');search.focus();});}
  lines.addEventListener('input',total);
  form.addEventListener('submit',e=>{if(mode==='sale'&&(serverDisabled||saleInvalid)){e.preventDefault();message.textContent='Review the quantities and prices before completing the sale.';}else if(!lines.children.length){e.preventDefault();message.textContent='Add at least one product to the basket.';search.focus();}else if(form.checkValidity()){form.querySelector('button[type=submit]').disabled=true;}});
  if(mode==='sale'){
   const saved=form.querySelector('.sale-restore');
   if(saved)JSON.parse(saved.textContent).forEach(product=>{add(product);const row=[...lines.children].find(r=>r.dataset.id===String(product.id));row.querySelector('[name=quantity]').value=product.quantity;row.querySelector('[name=unit_price]').required=false;});
   total();
   window.addEventListener('pageshow',()=>{submitButton.disabled=serverDisabled;total();});
  }
  if(mode==='restock'){
   const saved=form.querySelector('.restock-restore');
   if(saved)JSON.parse(saved.textContent).forEach(product=>{add(product);const row=[...lines.children].find(r=>r.dataset.id===String(product.id));row.querySelector('[name=quantity]').value=product.quantity;});
   total();
   window.addEventListener('pageshow',()=>{submitButton.disabled=serverDisabled;});
  }
  const initial=basket.querySelector('.basket-initial');if(initial)add(JSON.parse(initial.textContent));
 });
})();
