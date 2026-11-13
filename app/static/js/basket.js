(() => {
 document.querySelectorAll('.basket').forEach(basket=>{
  const mode=basket.dataset.mode,search=basket.querySelector('.basket-search'),results=basket.querySelector('.basket-results'),lines=basket.querySelector('.basket-lines'),message=basket.querySelector('.basket-message');
  const form=basket.closest('form'),submitButton=form.querySelector('button[type=submit]'),serverDisabled=submitButton.disabled;let timer,controller,sequence=0,basketInvalid=false,submitting=false;
  const redesignedSale = mode === 'sale' && form.hasAttribute('data-sale-redesign');
  const redesignedRestock = mode === 'restock' && form.hasAttribute('data-restock-redesign');
  const redesigned = redesignedSale || redesignedRestock;
  const stockPreview = form.querySelector('[data-restock-stock]');
  const totalDisplay = form.querySelector('.basket-total');
  const profitDisplay = form.querySelector('[data-sale-profit]');
  const money=value=>'₦'+Number(value).toLocaleString('en-NG',{minimumFractionDigits:redesigned && Number.isInteger(Number(value)) ? 0 : 2,maximumFractionDigits:2});
  const displayName = name => redesigned ? name.charAt(0).toUpperCase() + name.slice(1) : name;
  const counted=(count,noun)=>`${count} ${noun}${count===1?'':'s'}`;
  const restockUnits = {unit:'units', bottle:'bottles', pack:'packs', piece:'pieces', box:'boxes',
    bag:'bags', carton:'cartons', tin:'tins', can:'cans', sachet:'sachets', crate:'crates',
    pair:'pairs', dozen:'dozen', litre:'litres', kilogram:'kilograms'};
  const stockLabel = product => {
    if (!redesignedRestock) return `${product.stock} ${product.unit}`;
    const unit = product.unit.toLowerCase().trim();
    return `${product.stock} ${product.stock === 1 || !Object.hasOwn(restockUnits, unit) ? product.unit : restockUnits[unit]}`;
  };
  function total(){let amount=0,units=0,quantitiesValid=true,grossProfit=0,profitKnown=true;basketInvalid=false;
   const stockChanges = [];
   lines.querySelectorAll('.basket-line').forEach(row=>{
    const input=row.querySelector('[name=quantity]'),q=Number(input.value||0),priceInput=row.querySelector('.line-price'),price=Number(priceInput.value||0);
    const subtotal=q*price;let rowInvalid=!input.validity.valid||!priceInput.validity.valid||!Number.isFinite(subtotal);
    quantitiesValid=quantitiesValid&&input.validity.valid;units+=q;
    if(mode==='sale'||mode==='restock'){
     const warning=row.querySelector('.stock-warning'),stock=Number(row.dataset.stock);
     const excess=mode==='sale'&&q>stock;warning.textContent=excess?`Only ${stock} available. Reduce the quantity.`:(!input.validity.valid?'Enter a valid whole quantity within the supported stock range.':(!priceInput.validity.valid?'Enter a valid non-negative unit price or purchase cost with at most two decimal places.':''));
     warning.hidden=!warning.textContent;input.setAttribute('aria-invalid',String(excess||!input.validity.valid));
     priceInput.setAttribute('aria-invalid',String(!priceInput.validity.valid));
     if (mode === 'restock') {
       const validStock = input.validity.valid && Number.isInteger(q) && q > 0 && q + stock <= 2147483647;
       const after = row.querySelector('.expected-stock');
       if (redesignedRestock) {
         const change = validStock ? `${stock} to ${stock + q}` : '';
         after.textContent = change ? `Expected stock: ${change}` : 'Expected stock: review quantity';
         if (change) stockChanges.push(`${row.querySelector('legend').textContent}: ${change}`);
       } else {
         after.textContent = Number.isInteger(q) && q > 0 && q + stock <= 2147483647
           ? `Expected stock after restocking: ${q + stock}` : 'Expected stock: review quantity';
       }
     }
     rowInvalid=rowInvalid||excess;
    }
    basketInvalid=basketInvalid||rowInvalid;
    if(!rowInvalid){
      amount+=subtotal;
      if (profitDisplay) {
        if (row.dataset.unitCost === undefined || !priceInput.value) profitKnown=false;
        else grossProfit+=subtotal-q*Number(row.dataset.unitCost);
      }
    }
    row.querySelector('.line-total').textContent=rowInvalid?'Review inputs':money(subtotal);
   });
   const totalLabel = redesignedRestock ? 'Total cost' : 'Total';
   totalDisplay.textContent = basketInvalid ? (redesignedRestock ? 'Total cost unavailable. Review inputs.' : 'Total unavailable — review inputs') : totalLabel + ': ' + money(amount);
   if (stockPreview) {
     if (basketInvalid) stockPreview.textContent = 'Review quantities and costs to see the stock change.';
     else if (stockChanges.length > 1) stockPreview.textContent = `Stock preview: ${stockChanges[0]} · +${stockChanges.length - 1} more. See item rows.`;
     else stockPreview.textContent = stockChanges.length ? 'Stock preview: ' + stockChanges[0] : 'Add a product to preview its stock change.';
   }
   if (profitDisplay) profitDisplay.textContent = basketInvalid || !profitKnown
     ? 'Estimated gross profit unavailable'
     : 'Estimated gross profit: ' + money(Math.round(grossProfit * 100) / 100);
   if(mode==='sale'||mode==='restock'){
    const count=basket.querySelector('.basket-count'),empty=basket.querySelector('.basket-empty');
    if(count)count.textContent=counted(lines.children.length,'product')+' selected';
    if(empty)empty.hidden=lines.children.length>0;
    const review=basket.querySelector('.restock-review');if(review)review.textContent=`Review: ${counted(lines.children.length,'product')} · ${quantitiesValid?counted(units,'unit'):'Review quantities'} · ${basketInvalid?'Total unavailable — review inputs':money(amount)}`;
    submitButton.disabled=serverDisabled||basketInvalid||submitting;
   }
  }
  function add(product){const existing=[...lines.children].find(r=>r.dataset.id===String(product.id));if(existing){existing.querySelector('[name=quantity]').focus();message.textContent='This product is already in the basket. Change its quantity there.';return;}
   if(lines.children.length>=100){message.textContent='A transaction can contain up to 100 products.';return;}
   const row=document.createElement('fieldset');row.className='basket-line';row.dataset.id=product.id;row.dataset.stock=product.stock;
   if (redesignedSale && product.cost != null && product.cost !== '' && Number.isFinite(Number(product.cost))) row.dataset.unitCost=product.cost;
   const title=document.createElement('legend');title.textContent=displayName(product.name);row.append(title);
   const stock=document.createElement('small');stock.textContent=`${stockLabel(product)} currently available`;row.append(stock);stock.id=`${mode}-stock-${product.id}`;
   const pid=document.createElement('input');pid.type='hidden';pid.name='product_id';pid.value=product.id;row.append(pid);
   function field(labelText,name,value,step){const label=document.createElement('label');label.textContent=labelText;const input=document.createElement('input');input.type='number';input.name=name;input.value=value;input.min=name==='quantity'?'1':'0';input.step=step;input.inputMode=step==='1'?'numeric':'decimal';input.required=true;label.append(input);row.append(label);return input;}
   const qty=field(mode==='sale'?'Quantity':'Quantity received','quantity','1','1');qty.max=mode==='sale'?product.stock:2147483647-Number(product.stock);qty.setAttribute('aria-describedby',`${mode}-stock-${product.id} ${mode}-warning-${product.id}`);
   if (redesigned) {
    const label=qty.parentElement;
    const quantityLabel=document.createElement('span');
    quantityLabel.textContent=label.firstChild.textContent;
    quantityLabel.id=`${mode}-quantity-${product.id}`;
    const controls=document.createElement('div');
    controls.className='sb-sale-quantity';
    qty.setAttribute('aria-labelledby',quantityLabel.id);
    const stepButton=(direction,text,description)=>{
      const button=document.createElement('button');
      button.type='button';
      button.className='sb-button sb-button-secondary';
      button.textContent=text;
      button.setAttribute('aria-label',`${description} for ${product.name}`);
      button.addEventListener('click',()=>{
        if (direction < 0 && Number(qty.value) > 1) qty.stepDown();
        if (direction > 0 && Number(qty.value) < Number(qty.max)) qty.stepUp();
        total();
      });
      return button;
    };
    controls.append(stepButton(-1,'−','Decrease quantity'),qty,stepButton(1,'+','Increase quantity'));
    const group=document.createElement('div');
    group.append(quantityLabel,controls);
    label.replaceWith(group);
   }
   const price=field(mode==='sale'?'Unit price (₦)':'Purchase cost per unit (₦)',mode==='sale'?'unit_price':'unit_cost',mode==='sale'?product.price:product.cost,'0.01');price.className='line-price';price.setAttribute('aria-describedby',`${mode}-warning-${product.id}`);if(mode==='restock')price.max='9999999999.99';
   {const warning=document.createElement('p');warning.className='stock-warning';warning.id=`${mode}-warning-${product.id}`;warning.setAttribute('role','status');warning.hidden=true;row.append(warning);if(mode==='restock'){const after=document.createElement('small');after.className='expected-stock';row.append(after);}}
   const subtotal=document.createElement('strong');subtotal.className='line-total';row.append(subtotal);const remove=document.createElement('button');remove.type='button';remove.className='secondary-btn';remove.textContent='Remove item';remove.onclick=()=>{row.remove();message.textContent='';total();if(mode==='sale')search.focus();};row.append(remove);lines.append(row);if(!redesigned){results.replaceChildren();search.value='';}message.textContent='Product added. Add another or review your basket.';if(mode==='sale')search.focus();else qty.focus();total();
  }
  async function lookup(term,target=results){controller?.abort();controller=new AbortController();const mine=++sequence;target.textContent='Searching…';
   try{const response=await fetch(`${basket.dataset.lookup}?q=${encodeURIComponent(term)}`,{signal:controller.signal,headers:{Accept:'application/json'}});if(!response.ok)throw Error();const data=await response.json();if(mine!==sequence)return;target.replaceChildren();
    if(!data.products.length){target.textContent='No matching products. Try another name, SKU or category.';return;}
    data.products.forEach(product=>{const button=document.createElement('button');button.type='button';button.className='search-result';button.textContent=`${product.name} · ${product.stock} ${product.unit} · ${money(mode==='sale'?product.price:product.cost)}`;if (redesigned) {
      const name=document.createElement('strong');
      const price=document.createElement('span');
      const stock=document.createElement('small');
      name.textContent=displayName(product.name);
      price.textContent = redesignedRestock ? 'Last cost: ' + money(product.cost) : money(product.price);
      stock.textContent=`${stockLabel(product)} left`;
      button.replaceChildren(name,price,stock);
    }
    button.disabled=mode==='sale'&&product.stock===0;button.onclick=()=>add(product);target.append(button);});
   }catch(error){if(error.name!=='AbortError'&&mine===sequence)target.textContent='Could not search products. Check your connection and try again.';}
  }
  search.addEventListener('input',()=>{clearTimeout(timer);controller?.abort();sequence++;const term=search.value.trim();if(!term){if(redesigned&&!serverDisabled)lookup('');else results.replaceChildren();return;}timer=setTimeout(()=>lookup(term),200);});
  search.addEventListener('keydown',e=>{if(e.key==='Enter'){e.preventDefault();clearTimeout(timer);if(search.value.trim())lookup(search.value.trim());}});
  const browse=basket.querySelector('.basket-browse'),browseResults=basket.querySelector('.basket-browse-results'),picker=basket.querySelector('.sale-product-picker,.restock-product-picker');
  if(picker)picker.addEventListener('click',()=>{lookup('',browseResults||results);});
  if(browse){browse.addEventListener('toggle',()=>{if(browse.open&&!browseResults.children.length)lookup('',browseResults);});browse.addEventListener('keydown',event=>{if(event.key==='Escape'){browse.open=false;search.focus();}});}
  lines.addEventListener('input',total);
  form.addEventListener('submit',e=>{if((mode==='sale'||mode==='restock')&&(serverDisabled||basketInvalid)){e.preventDefault();message.textContent=mode==='sale'?'Review the quantities and prices before completing the sale.':'Review the quantities and purchase costs before completing the restock.';}else if(!lines.children.length){e.preventDefault();message.textContent='Add at least one product to the basket.';search.focus();}else if(form.checkValidity()){submitting=true;form.querySelector('button[type=submit]').disabled=true;}});
  if(mode==='sale'){
   const saved=form.querySelector('.sale-restore');
   if(saved)JSON.parse(saved.textContent).forEach(product=>{add(product);const row=[...lines.children].find(r=>r.dataset.id===String(product.id));row.querySelector('[name=quantity]').value=product.quantity;row.querySelector('[name=unit_price]').required=false;});
   total();
   window.addEventListener('pageshow',()=>{submitting=false;submitButton.disabled=serverDisabled;total();});
  }
  if(mode==='restock'){
   const saved=form.querySelector('.restock-restore');
   if(saved)JSON.parse(saved.textContent).forEach(product=>{add(product);const row=[...lines.children].find(r=>r.dataset.id===String(product.id));row.querySelector('[name=quantity]').value=product.quantity;});
   total();
   window.addEventListener('pageshow',()=>{submitting=false;submitButton.disabled=serverDisabled;total();});
  }
  const initial=basket.querySelector('.basket-initial');if(initial)add(JSON.parse(initial.textContent));
  if (redesigned && !serverDisabled) lookup(search.value.trim());
 });
})();
