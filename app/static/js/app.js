const menuButton = document.getElementById("menuButton");
const sidebar = document.getElementById("sidebar");

if (menuButton && sidebar) {
    const closeButton=document.getElementById('sidebarClose'),backdrop=document.getElementById('sidebarBackdrop'),main=document.querySelector('.main-panel');
    const mobile=window.matchMedia?.('(max-width: 820px)');
    const isMobile=()=>mobile?.matches ?? false;
    function setMenu(open,restoreFocus=false){
        open=Boolean(open&&isMobile());
        sidebar.classList.toggle('open',open);
        menuButton.setAttribute('aria-expanded',String(open));
        menuButton.setAttribute('aria-label',open?'Close navigation':'Open navigation');
        if(backdrop)backdrop.hidden=!open;
        document.body.classList.toggle('navigation-open',open);
        sidebar.inert=isMobile()&&!open;
        if(isMobile()&&!open)sidebar.setAttribute('aria-hidden','true');else sidebar.removeAttribute('aria-hidden');
        if(main)main.inert=open;
        if(open){sidebar.setAttribute('role','dialog');sidebar.setAttribute('aria-modal','true');sidebar.setAttribute('aria-label','Navigation');closeButton?.focus();}
        else{sidebar.removeAttribute('role');sidebar.removeAttribute('aria-modal');sidebar.removeAttribute('aria-label');if(restoreFocus)menuButton.focus();}
    }
    menuButton.addEventListener('click',()=>setMenu(!sidebar.classList.contains('open'),true));
    closeButton?.addEventListener('click',()=>setMenu(false,true));
    backdrop?.addEventListener('click',()=>setMenu(false,true));
    sidebar.querySelectorAll('a').forEach(link=>link.addEventListener('click',()=>setMenu(false)));
    document.addEventListener('keydown',event=>{
        if(!sidebar.classList.contains('open'))return;
        if(event.key==='Escape'){event.preventDefault();setMenu(false,true);}
        if(event.key==='Tab'){
            const targets=[...sidebar.querySelectorAll('a[href],button:not([disabled]),input:not([disabled]),[tabindex="0"]')]
                .filter(control=>control.type!=='hidden'&&!control.hidden);
            const first=targets[0],last=targets[targets.length-1];
            if(event.shiftKey&&document.activeElement===first){event.preventDefault();last?.focus();}
            else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first?.focus();}
        }
    });
    if(mobile?.addEventListener)mobile.addEventListener('change',()=>setMenu(false));
    else mobile?.addListener?.(()=>setMenu(false));
    window.addEventListener('pagehide',()=>setMenu(false));
    setMenu(false);
}

const profileButton=document.getElementById("profileButton"),profileDropdown=document.getElementById("profileDropdown");if(profileButton&&profileDropdown){profileButton.addEventListener("click",()=>{profileDropdown.classList.toggle("open");profileButton.setAttribute("aria-expanded",profileDropdown.classList.contains("open"))});document.addEventListener("click",e=>{if(!e.target.closest(".profile-menu"))profileDropdown.classList.remove("open")})}

const csrf=document.querySelector('meta[name="csrf-token"]')?.content;document.querySelectorAll('form[method="POST"],form[method="post"]').forEach(form=>{if(csrf&&!form.querySelector('[name="csrf_token"]')){const input=document.createElement("input");input.type="hidden";input.name="csrf_token";input.value=csrf;form.prepend(input)}});if(!document.body.classList.contains("customer-app"))document.querySelectorAll(".flash").forEach(el=>setTimeout(()=>el.classList.add("flash-hide"),4500));

// Keep business tables readable as labelled cards on narrow screens.
document.querySelectorAll('table.responsive-table').forEach(table=>{
 table.querySelector('tr:first-child')?.classList.add('table-head-row');
 const labels=[...table.querySelectorAll('tr:first-child th')].map(cell=>cell.textContent.trim());
 table.querySelectorAll('tr').forEach(row=>[...row.querySelectorAll('td')].forEach((cell,index)=>{if(labels[index]&&!cell.hasAttribute('data-label'))cell.dataset.label=labels[index];}));
});
