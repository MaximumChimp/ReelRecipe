// static/js/navbar.js
document.addEventListener('DOMContentLoaded', () => {
    const headerElement = document.querySelector('.main-header');
    
    if (!headerElement) return;

    const handleScroll = () => {
        if (window.scrollY > 20) {
            headerElement.classList.add('header-scrolled');
        } else {
            headerElement.classList.remove('header-scrolled');
        }
    };

    window.addEventListener('scroll', handleScroll, { passive: true });
});