can.Control("GGRC.Controllers.TreeFilter", {
  
}, {
  
  init : function() {
    this._super && this._super.apply(this, arguments);
    this.options.states = {};
    this.on();
  }

  , "input, select change" : function(el, ev) {
    var name = el.attr("name");
    if(el.is(".hasDatepicker")) {
      this.options.states[name] = moment(el.val(), "MM/DD/YYYY");
    } else {
      this.options.states[name] = el.val();
    }
    this.statechange(this.options.states);
    ev.stopPropagation();
  }

  , "statechange" : function(states) {
    var that = this;
    this.element
    .closest(".tree-structure")
    .children(":has(> [data-model],:data(model))").each(function(i, el) {
      var model = $(el).children("[data-model],:data(model)").data("model");
      if(can.reduce(Object.keys(states), function(st, key) {
        var val = states[key]
        , test = that.resolve_object(model, key);
        
        if(val.isAfter) {
          if(!test || !moment(test).isAfter(val)) {
            return false;
          } else {
            return st;
          }
        } else if(val && (!test || !~test.toUpperCase().indexOf(val.toUpperCase()))) {
          return false;
        } else {
          return st;
        }
      }, true)) {
        $(el).show();
      } else {
        $(el).hide();
      }
    });
  }

  , resolve_object : function(obj, path) {
    path = path.split(".");
    can.each(path, function(prop) {
      obj = obj.attr ? obj.attr(prop) : obj.prop;
      obj = obj && obj.reify ? obj.reify() : obj;
      return obj != null; //stop iterating in case of null/undefined.
    });
    return obj;
  }

  //this controller is used in sticky headers which clone the original element.
  // It should only be destroyed when the original element is destroyed, not any clone.
  , destroy : function(el, ev) {
    var sticky;
    if(this.element.is(el)) {
      this._super.apply(this, arguments);
    } else if((sticky = this.element.data("sticky")) && el.is(sticky.clone)) {
      delete sticky.clone;
    }
  }

});
