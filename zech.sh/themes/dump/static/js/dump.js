document.addEventListener('DOMContentLoaded', function() {
    var toggle = document.querySelector('.zech-user-toggle');
    var menu = document.querySelector('.zech-user-menu');
    if (toggle && menu) {
        toggle.addEventListener('click', function(e) {
            e.stopPropagation();
            menu.classList.toggle('open');
        });
        document.addEventListener('click', function() {
            menu.classList.remove('open');
        });
    }
    document.querySelectorAll('.sk-flash-dismiss').forEach(function(btn) {
        btn.addEventListener('click', function() {
            this.closest('.sk-flash').remove();
        });
    });
});
