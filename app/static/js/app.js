const menuButton = document.getElementById("menuButton");
const sidebar = document.getElementById("sidebar");

if (menuButton && sidebar) {
    menuButton.addEventListener("click", () => {
        sidebar.classList.toggle("open");
    });
}

const profileButton=document.getElementById("profileButton"),profileDropdown=document.getElementById("profileDropdown");if(profileButton&&profileDropdown){profileButton.addEventListener("click",()=>{profileDropdown.classList.toggle("open");profileButton.setAttribute("aria-expanded",profileDropdown.classList.contains("open"))});document.addEventListener("click",e=>{if(!e.target.closest(".profile-menu"))profileDropdown.classList.remove("open")})}

const csrf=document.querySelector('meta[name="csrf-token"]')?.content;document.querySelectorAll('form[method="POST"],form[method="post"]').forEach(form=>{if(csrf&&!form.querySelector('[name="csrf_token"]')){const input=document.createElement("input");input.type="hidden";input.name="csrf_token";input.value=csrf;form.prepend(input)}});document.querySelectorAll(".flash").forEach(el=>setTimeout(()=>el.classList.add("flash-hide"),4500));
