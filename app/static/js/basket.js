(() => {
 document.querySelectorAll('.basket').forEach(basket=>{
  const mode=basket.dataset.mode,search=basket.querySelector('.basket-search'),results=basket.querySelector('.basket-results'),lines=basket.querySelector('.basket-lines'),message=basket.querySelector('.basket-message');
  const form=basket.closest('form');let timer,controller,sequence=0;
  const money=value=>'₦'+Number(value).toLocaleString('en-NG',{minimumFractionDigits:2,maximumFractionDigits:2});
  function total(){let amount=0;lines.querySelectorAll('.basket-line').forEach(row=>{const q=Number(row.querySelector('[name=quantity]').value||0),price=Number(row.querySelector('.line-price').value||0);amount+=q*price;row.querySelector('.line-total').textContent=money(q*price);});basket.querySelector('.basket-total').textContent='Total: '+money(amount);}
  function add(product){const existing=[...lines.children].find(r=>r.dataset.id===String(product.id));if(existing){existing.querySelector('[name=quantity]').focus();message.textContent='This product is already in the basket. Change its quantity there.';return;}
   if(lines.children.length>=100){message.textContent='A transaction can contain up to 100 products.';return;}
   const row=document.createElement('fieldset');row.className='basket-line';row.dataset.id=product.id;
   const title=document.createElement('legend');title.textContent=product.name;row.append(title);
   const stock=document.createElement('small');stock.textContent=`${product.stock} ${product.unit} currently available`;row.append(stock);
   const pid=document.createElement('input');pid.type='hidden';pid.name='product_id';pid.value=product.id;row.append(pid);
   function field(labelText,name,value,step){const label=document.createElement('label');label.textContent=labelText;const input=document.createElement('input');input.type='number';input.name=name;input.value=value;input.min=name==='quantity'?'1':'0';input.step=step;input.inputMode=step==='1'?'numeric':'decimal';input.required=true;label.append(input);row.append(label);return input;}
   const qty=field(mode==='sale'?'Quantity':'Quantity received','quantity','1','1');if(mode==='sale')qty.max=product.stock;
   const price=field(mode==='sale'?'Unit price (₦)':'Purchase cost per unit (₦)',mode==='sale'?'unit_price':'unit_cost',mode==='sale'?product.price:product.cost,'0.01');price.className='line-price';
   const subtotal=document.createElement('strong');subtotal.className='line-total';row.append(subtotal);const remove=document.createElement('button');remove.type='button';remove.className='secondary-btn';remove.textContent='Remove item';remove.onclick=()=>{row.remove();total();};row.append(remove);lines.append(row);results.replaceChildren();search.value='';message.textContent='Product added. Add another or review your basket.';qty.focus();total();
  }
  async function lookup(term,barcode=false){controller?.abort();controller=new AbortController();const mine=++sequence;results.textContent='Searching…';
   try{const response=await fetch(`${basket.dataset.lookup}?${barcode?'barcode':'q'}=${encodeURIComponent(term)}`,{signal:controller.signal,headers:{Accept:'application/json'}});if(!response.ok)throw Error();const data=await response.json();if(mine!==sequence)return;results.replaceChildren();
    if(barcode&&data.products.length){add(data.products[0]);return;}
    if(!data.products.length){results.textContent=barcode?'Product not in your active catalogue. Add or restore it before recording this transaction.':'No matching products. Try another name, SKU or barcode.';if(barcode){const link=document.createElement('a');link.className='secondary-btn button-link';link.textContent='Add Product';link.href=`${basket.dataset.create}?barcode=${encodeURIComponent(term)}`;results.append(link);}return;}
    data.products.forEach(product=>{const button=document.createElement('button');button.type='button';button.className='search-result';button.textContent=`${product.name} · ${product.stock} ${product.unit} · ${money(mode==='sale'?product.price:product.cost)}`;button.disabled=mode==='sale'&&product.stock===0;button.onclick=()=>add(product);results.append(button);});
   }catch(error){if(error.name!=='AbortError'&&mine===sequence)results.textContent='Could not search products. Check your connection and try again.';}
  }
  search.addEventListener('input',()=>{clearTimeout(timer);controller?.abort();sequence++;const term=search.value.trim();if(!term){results.replaceChildren();return;}timer=setTimeout(()=>lookup(term),200);});
  search.addEventListener('keydown',e=>{if(e.key==='Enter'){e.preventDefault();clearTimeout(timer);if(search.value.trim())lookup(search.value.trim());}});
  basket.querySelector('.basket-scan').onclick=()=>window.StockBridgeScan(code=>lookup(code,true));lines.addEventListener('input',total);
  form.addEventListener('submit',e=>{if(!lines.children.length){e.preventDefault();message.textContent='Add at least one product to the basket.';search.focus();}else if(form.checkValidity()){form.querySelector('button[type=submit]').disabled=true;}});
  const initial=basket.querySelector('.basket-initial');if(initial)add(JSON.parse(initial.textContent));
 });
})();
