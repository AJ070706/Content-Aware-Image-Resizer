import tkinter as tk
from tkinter import filedialog
from tkinter import ttk
from PIL import Image, ImageTk
import numpy as np
import main

class SeamCarverGUI:
    def __init__(self, master):
        self.master = master
        master.title("Seam Carver")
        master.minsize(700, 500)
        master.configure(bg="#1e1e1e")

        self.original_image = None
        self.modified_image = None
        self.photo = None
        self.target_width = tk.IntVar()
        self.target_height = tk.IntVar()
        self.display_mode = tk.StringVar(value="fit")

        top_frame = tk.Frame(master, bg="#1e1e1e")
        top_frame.pack(fill=tk.X, padx=10, pady=5)

        self.load_button = tk.Button(top_frame, text="Load Image", command=self.load_image, bg="#ff8c00", fg="white", relief="flat")
        self.load_button.pack(side=tk.LEFT, padx=5, fill=tk.X, expand=True)

        self.save_button = tk.Button(top_frame, text="Save Image", command=self.save_image, bg="#ff8c00", fg="white", relief="flat")
        self.save_button.pack(side=tk.LEFT, padx=5, fill=tk.X, expand=True)

        self.reload_button = tk.Button(top_frame, text="Reload Original", command=self.reload_image, bg="#ff8c00", fg="white", relief="flat")
        self.reload_button.pack(side=tk.LEFT, padx=5, fill=tk.X, expand=True)

        tk.Label(top_frame, text="Display Mode:", bg="#1e1e1e", fg="white").pack(side=tk.LEFT, padx=5)
        self.display_mode = tk.StringVar(value="fit")  # "fit", "true", "true*0.5", "true*0.25"
        mode_menu = ttk.Combobox(
            top_frame,
            textvariable=self.display_mode,
            values=["fit", "true", "true*0.5", "true*0.25"],
            width=8,
            state="readonly"
        )
        mode_menu.pack(side=tk.LEFT)
        mode_menu.bind("<<ComboboxSelected>>", lambda e: self.display_image(self.modified_image))

        self.canvas = tk.Canvas(master, bg="#1e1e1e", highlightthickness=0)
        self.canvas.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        bottom_frame = tk.Frame(master, bg="#1e1e1e")
        bottom_frame.pack(fill=tk.X, padx=10, pady=5)

        tk.Label(bottom_frame, text="Width:", bg="#1e1e1e", fg="white").grid(row=0, column=0, padx=5)
        tk.Label(bottom_frame, text="Height:", bg="#1e1e1e", fg="white").grid(row=1, column=0, padx=5)

        self.width_entry = tk.Entry(bottom_frame, textvariable=self.target_width, width=6, bg="#2e2e2e", fg="white", insertbackground="white")
        self.width_entry.grid(row=0, column=1, padx=2)

        self.height_entry = tk.Entry(bottom_frame, textvariable=self.target_height, width=6, bg="#2e2e2e", fg="white", insertbackground="white")
        self.height_entry.grid(row=1, column=1, padx=2)
        
        self.width_set = tk.Button(bottom_frame, text="Set", bg="#ff8c00", fg="white", width=4, command=self.update_width_from_entry)
        self.width_set.grid(row=0, column=4, padx=2)

        self.height_set = tk.Button(bottom_frame, text="Set", bg="#ff8c00", fg="white", width=4, command=self.update_height_from_entry)
        self.height_set.grid(row=1, column=4, padx=2)
        
        self.width_dec = tk.Button(bottom_frame, text="-", bg="#2e2e2e", fg="white", width=2, command=lambda: self.increment_width(-1))
        self.width_dec.grid(row=0, column=2, padx=2)
        self.width_inc = tk.Button(bottom_frame, text="+", bg="#2e2e2e", fg="white", width=2, command=lambda: self.increment_width(1))
        self.width_inc.grid(row=0, column=3, padx=2)

        self.height_dec = tk.Button(bottom_frame, text="-", bg="#2e2e2e", fg="white", width=2, command=lambda: self.increment_height(-1))
        self.height_dec.grid(row=1, column=2, padx=2)
        self.height_inc = tk.Button(bottom_frame, text="+", bg="#2e2e2e", fg="white", width=2, command=lambda: self.increment_height(1))
        self.height_inc.grid(row=1, column=3, padx=2)

        # self.width_slider = ttk.Scale(bottom_frame, from_=1, to=1, orient="horizontal", command=self.update_width)
        # self.width_slider.grid(row=1, column=0, columnspan=4, sticky="ew", padx=5, pady=5)
        
        # self.height_slider = ttk.Scale(bottom_frame, from_=1, to=1, orient="horizontal", command=self.update_height)
        # self.height_slider.grid(row=3, column=0, columnspan=4, sticky="ew", padx=5, pady=5)

        master.bind("<Configure>", self.on_resize)

        style = ttk.Style()
        style.theme_use('default')
        style.configure("TScale", troughcolor="#2e2e2e", background="#ff8c00")


    def load_image(self):
        path = filedialog.askopenfilename()
        if path:
            self.original_image = Image.open(path).convert("RGB")
            self.modified_image = self.original_image.copy()
            w, h = self.modified_image.width, self.modified_image.height

            self.target_width.set(w)
            self.target_height.set(h)

            # self.width_slider.config(to=w*2)
            # self.height_slider.config(to=h*2)
            # self.width_slider.set(w)
            # self.height_slider.set(h)

            self.display_image(self.modified_image)

    def save_image(self):
        if self.modified_image:
            path = filedialog.asksaveasfilename(defaultextension=".png")
            if path:
                self.modified_image.save(path)

    def reload_image(self):
        if self.original_image:
            self.modified_image = self.original_image.copy()
            self.target_width.set(self.modified_image.width)
            self.target_height.set(self.modified_image.height)
            # self.width_slider.set(self.modified_image.width)
            # self.height_slider.set(self.modified_image.height)
            self.display_image(self.modified_image)

    def update_width(self, val):
        new_width = int(float(val))
        self.target_width.set(new_width)
        self.apply_seam_carving()

    def update_height(self, val):
        new_height = int(float(val))
        self.target_height.set(new_height)
        self.apply_seam_carving()

    def update_width_from_entry(self):
        val = self.target_width.get()
        val = max(1, min(val, self.original_image.width*2))
        self.target_width.set(val)
        # self.width_slider.set(val)
        self.apply_seam_carving()

    def update_height_from_entry(self):
        val = self.target_height.get()
        val = max(1, min(val, self.original_image.height*2))
        self.target_height.set(val)
        # self.height_slider.set(val)
        self.apply_seam_carving()

    def increment_width(self, delta):
        max_val = self.original_image.width * 2
        new_val = self.target_width.get() + delta
        new_val = max(1, min(new_val, max_val))
        self.target_width.set(new_val)
        # self.width_slider.set(new_val)
        self.apply_seam_carving()

    def increment_height(self, delta):
        max_val = self.original_image.height * 2
        new_val = self.target_height.get() + delta
        new_val = max(1, min(new_val, max_val))
        self.target_height.set(new_val)
        # self.height_slider.set(new_val)
        self.apply_seam_carving()

    def apply_seam_carving(self):
        if self.modified_image:
            img_array = np.array(self.original_image)
            # TODO: Call C++ backend:
            # img_array = main.resize_image(img_array, self.target_width.get(), self.target_height.get())
            img_array = main.highlight(
                img_array,
                self.target_width.get(),
                self.target_height.get()
            )
            self.modified_image = Image.fromarray(img_array.astype(np.uint8))
            self.display_image(self.modified_image)

    def display_image(self, image):
        if not image:
            return
        img = image.copy()
        mode = self.display_mode.get()
        canvas_width = self.canvas.winfo_width()
        canvas_height = self.canvas.winfo_height()

        if mode == "fit":
            img.thumbnail((canvas_width, canvas_height), Image.LANCZOS)
        elif mode == "true*0.5":
            w, h = img.size
            img = img.resize((w//2, h//2), Image.LANCZOS)
        elif mode == "true*0.25":
            w, h = img.size
            img = img.resize((w//4, h//4), Image.LANCZOS)

        self.photo = ImageTk.PhotoImage(img)
        self.canvas.delete("all")
        self.canvas.create_image(0, 0, anchor=tk.NW, image=self.photo)

    def on_resize(self, event):
        if self.display_mode.get() == "fit":
            self.display_image(self.modified_image)

if __name__ == "__main__":
    root = tk.Tk()
    app = SeamCarverGUI(root)
    root.mainloop()
