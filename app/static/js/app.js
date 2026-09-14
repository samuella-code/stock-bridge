const menuButton = document.getElementById("menuButton");
const sidebar = document.getElementById("sidebar");

if (menuButton && sidebar) {
    menuButton.addEventListener("click", () => {
        sidebar.classList.toggle("open");
    });
}

const profileButton=document.getElementById("profileButton"),profileDropdown=document.getElementById("profileDropdown");if(profileButton&&profileDropdown){profileButton.addEventListener("click",()=>{profileDropdown.classList.toggle("open");profileButton.setAttribute("aria-expanded",profileDropdown.classList.contains("open"))});document.addEventListener("click",e=>{if(!e.target.closest(".profile-menu"))profileDropdown.classList.remove("open")})}
